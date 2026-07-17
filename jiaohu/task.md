# 工装测试上位机自动化联动任务清单

---

## 版本记录

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|----------|
| v1.0 | 2026-07-16 | — | 初始版本，基于 requirement.md v1.0 + design.md v1.1 |

---

## 任务约定

- 每个任务包含：**任务名 / 需求追溯 / 产出物 / 依赖 / 预估工作量**
- 优先级标记：**P0**=阻塞项(无此无法下一阶段)，**P1**=主要功能，**P2**=辅助功能
- 阶段之间为串行，同一阶段内任务可并行
- 完成标志：代码编写完成 + 自测通过（或在 tests/ 下有对应测试）

---

## 阶段一：基础设施搭建（2 文件，约 1 天）

### Task 1.1: 数据库迁移脚本
- **需求追溯**: IR-01（4 张新表）、NFR-02.4（安全写入）
- **设计参考**: design.md §4.1 migration_v1.sql
- **产出物**: `auto_coordinator/migration_v1.sql`
- **依赖**: 无
- **优先级**: P0
- **预估**: 0.5h

**实施内容**:
1. 编写 SQL 脚本，创建 4 张表 (`channel_mapping`, `link_rules`, `link_run_state`, `link_events`) 和 3 个索引
2. 使用 `CREATE TABLE IF NOT EXISTS` 确保幂等
3. 验证：在 SQLite 中执行后 `SELECT name FROM sqlite_master` 可见 4 张表

---

### Task 1.2: 联动引擎配置骨架
- **需求追溯**: NFR-04.2（参数集中管理）、NFR-01.1（控制周期配置）
- **设计参考**: design.md §8.1 auto_coordinator_config.json
- **产出物**: `auto_coordinator/config.json`
- **依赖**: 无
- **优先级**: P0
- **预估**: 0.3h

**实施内容**:
1. 创建 JSON 配置文件，包含所有可调参数：
   `control_interval_ms`, `max_deviation_hw_v`, `compensation_damping`,
   `compensation_max_per_cycle_v`, `compensation_retry_max`, `slope_reduce_default`,
   `slope_restore_cycles`, `alive_timeout_s`, `min_slope`, `state_retention_days`, `log_level`
2. 创建 `auto_coordinator/__init__.py`（空文件或包含版本号）

---

## 阶段二：核心引擎模块（8 文件，约 3-5 天）

### Task 2.1: DataReader（数据读取层）
- **需求追溯**: FR-02.1/FR-02.2(读电压/读NTC状态)、FR-04.1(读阈值)、FR-11(存活检测)
- **设计参考**: design.md §3.2 + §3.2.1 NTCConverter
- **产出物**: `auto_coordinator/data_reader.py`
- **依赖**: Task 1.1（DB 表需已创建用于验证 SQL）
- **优先级**: P0
- **预估**: 4h

**实施内容**:
1. 实现 `DataReader` 类：
   - `__init__()`: 初始化路径（BASE_DIR → jiaohu/），加载 NTC 曲线 `_load_curves()`
   - `_load_curves()`: 调用 `from NTC import load_ntc_curves_from_excel` 加载 `NTC.xlsx`
   - `load_mapping()`: 从 `channel_mapping` 表读取绑定/容差/补偿开关
   - `read_voltage_data()`: 读 `voltage_data` 表最后一行 → `list[float]`×18
   - `read_ntc_state()`: 读 `NTC.json` 三遍比较一致性，提取 temps/curves/slopes/auto_run/limits/pullup_resistors/ch_names/heartbeat
   - `read_thresholds()`: 读 `config.json` → `thresholds`
   - `calc_expected_voltage(temp, curve_name, pullup)`: 创建 NTCConverter 实例并查表
   - `check_alive(timeout)`: NTC.py 检查 `_heartbeat.timestamp`，1.py 检查 `voltage_data` 最新时间戳
   - `_read_json_safe(path)`: 容错读 JSON
2. 实现 `NTCConverter` 类：
   - `__init__()`: 构建正向电压缓存(voltage_cache) 和 逆向缓存(inverse_cache)
   - `_calc_voltage(t)`: 线性插值计算 NTC 分压电压，钳位 0~5V
   - `temp2voltage(t)`: O(1) 正向查表
   - `_build_inverse_cache()`: 10mV 步进构建电压→温度逆向表
   - `voltage2temp_approx(v)`: O(1) 逆向查表
3. 自测：能读取 `NTC.xlsx` 加载曲线、能查询 `voltage_data.db` 返回 18 路数据

---

### Task 2.2: NTCJsonWriter（共享原子写入器）
- **需求追溯**: IR-03(控制指令通道)、NFR-02.4(原子写入)、NFR-05.1(电压钳位)
- **设计参考**: design.md §3.2.1
- **产出物**: `auto_coordinator/ntc_json_writer.py`
- **依赖**: 无
- **优先级**: P0
- **预估**: 2h

**实施内容**:
1. 实现 `NTCJsonWriter` 类：
   - `__init__(ntc_json_path)`: 存储路径、临时文件路径、内部 `threading.Lock`
   - `write_cmd(ntc_ch, **kwargs)`: 锁内读-合并-写，将指令合并到 `_external_command[str(ntc_ch-1)]`
   - `write_mode(mode)`: 写入 NTC.json 顶层 `_mode` 字段 ('coordinator'/'standalone')
   - `write_reset_all_outputs()`: 写入 6 个通道 temp=25.0, slope=0.0, auto_run=False
   - `clear_commands()`: 清空 `_external_command`
   - `_read_cfg()` / `_write_cfg(cfg)`: 读 JSON / 写临时文件+原子 rename
2. 自测：模拟并发写入，验证 temp file + replace 原子性，验证 lock 防止覆盖

---

### Task 2.3: DeviationChecker（偏差检测）
- **需求追溯**: FR-02.4（三级判定）
- **设计参考**: design.md §3.3
- **产出物**: `auto_coordinator/deviation_checker.py`
- **依赖**: 无
- **优先级**: P0
- **预估**: 0.5h

**实施内容**:
1. 实现 `DeviationChecker` 类：
   - `HW_FAULT_THRESHOLD = 0.5`
   - `check(deviation, tolerance) → str`: 返回 'normal' / 'compensate' / 'hw_fault'
2. 自测：`deviation=0.02, tol=0.05 → 'normal'`，`deviation=0.10, tol=0.05 → 'compensate'`，`deviation=0.60, tol=0.05 → 'hw_fault'`

---

### Task 2.4: Compensator（自动补偿）
- **需求追溯**: FR-03(自动补偿)、NFR-05(安全钳位)
- **设计参考**: design.md §3.4
- **产出物**: `auto_coordinator/compensator.py`
- **依赖**: Task 2.1(DataReader)、Task 2.2(NTCJsonWriter)
- **优先级**: P0
- **预估**: 2h

**实施内容**:
1. 实现 `Compensator` 类：
   - `__init__(data_reader, ntc_writer)`: 注入依赖
   - `calc(ntc_ch, deviation, compensation_counts)`: 检查重试上限 → `-deviation * DAMPING` 钳位到 ±MAX_PER_CYCLE
   - `apply(ntc_ch, compensation_v)`: 读取当前温度 → 计算期望电压 → `target_v = expected_v + compensation_v` → 钳位 → `voltage2temp_approx` 反算 → `ntc_writer.write_cmd(ntc_ch, temp=target_temp)`
   - `is_compensation_limit(ntc_ch, compensation_counts)`: 检查计数 ≥ MAX_RETRY
2. 自测：模拟 deviation +0.1V 场景 → 补偿量 = -0.05V → 温度下调

---

### Task 2.5: SlopeGuard（斜率卫士）
- **需求追溯**: FR-04(电压超标降斜率)、FR-04.4(再次降斜)
- **设计参考**: design.md §3.5
- **产出物**: `auto_coordinator/slope_guard.py`
- **依赖**: Task 2.2(NTCJsonWriter)
- **优先级**: P0
- **预估**: 3h

**实施内容**:
1. 实现 `SlopeGuard` 类：
   - `__init__(data_reader, ntc_writer)`: 注入依赖，维护 `state` dict
   - `reduce(ntc_ch, current_slope, factor)`: 检查降级次数上限 → `new_slope = max(MIN_SLOPE, current_slope * factor)` → 递增 reduce_level → `ntc_writer.write_cmd(ntc_ch, slope=new_slope)`
   - `check_re_reduce(ntc_ch, actual_v)`: 检测连续上升 3 周期 → 再次降斜 + 返回 True
   - `try_restore(ntc_ch, actual_v, threshold)`: 回落 5 周期后恢复 original_slope → 清除 state
2. 自测：降斜 → 恢复场景；降斜后连续上升 → 再次降斜 + 告警标记

---

### Task 2.6: LinkTrigger（联动触发器）
- **需求追溯**: FR-05(通道联动)
- **设计参考**: design.md §3.6
- **产出物**: `auto_coordinator/link_trigger.py`
- **依赖**: Task 2.5(SlopeGuard)
- **优先级**: P1
- **预估**: 1.5h

**实施内容**:
1. 实现 `LinkTrigger` 类：
   - `__init__(slope_guard)`: 注入 SlopeGuard，初始化 `_rules_cache`
   - `load_rules(db_path)`: 从 `link_rules` 表加载 `(trigger_ch, linked_ch, multiplier)` 列表
   - `on_over_threshold(trigger_ch, actual_v, thresholds, mapping)`: 遍历规则 → 匹配触发通道 → 对联动通道调用 `slope_guard.reduce(linked, current_slope, multiplier)`
2. 自测：加载规则后模拟 CH1 超限 → CH2 斜率降低

---

### Task 2.7: RampDriver（斜坡驱动器）
- **需求追溯**: FR-06(自动斜坡推进)
- **设计参考**: design.md §3.7
- **产出物**: `auto_coordinator/ramp_driver.py`
- **依赖**: Task 2.2(NTCJsonWriter)
- **优先级**: P0
- **预估**: 1.5h

**实施内容**:
1. 实现 `RampDriver` 类：
   - `__init__(data_reader, ntc_writer)`: 注入依赖
   - `step(ntc_ch, ntc_state, ch_idx) → bool`: 计算 delta = (slope/60)*0.1 → `new_temp = current_temp + delta` → 检查高温/低温限幅 → 钳位 → `ntc_writer.write_cmd(ntc_ch, temp=new_temp)` → 返回是否限幅
2. 自测：正向斜率推进到达上限 → 停在 limit_high；负向到达下限 → 停在 limit_low

---

### Task 2.8: LinkLogger（日志记录器）
- **需求追溯**: FR-09(事件日志)、FR-02.5(状态日志)、NFR-01.4(异步批量写入)
- **设计参考**: design.md §3.8
- **产出物**: `auto_coordinator/link_logger.py`
- **依赖**: 无
- **优先级**: P1
- **预估**: 2h

**实施内容**:
1. 实现 `LinkLogger` 类：
   - `__init__(db_path)`: 初始化事件队列(maxlen=100)和状态队列(maxlen=50)
   - `log(event_type, ntc_channel, description, detail)`: 追加到事件队列
   - `write_state_snapshot(state)`: 追加每条通道状态到状态队列
   - `flush()`: 批量 INSERT 事件 + 状态 → commit
2. 自测：log 一条事件后 flush → SQLite 可查到记录

---

## 阶段三：引擎组装（1 文件，约 1 天）

### Task 3.1: AutoCoordinator 引擎核心
- **需求追溯**: FR-02~FR-06, FR-10, FR-11 全部联动控制逻辑
- **设计参考**: design.md §3.1
- **产出物**: `auto_coordinator/engine.py`
- **依赖**: Task 2.1~2.8 全部
- **优先级**: P0
- **预估**: 3h

**实施内容**:
1. 实现 `AutoCoordinator` 类：
   - `__init__()`: 创建 NTCJsonWriter 实例 → 依次创建 DataReader → DeviationChecker → Compensator → SlopeGuard → LinkTrigger → RampDriver → LinkLogger；初始化 compensation_counts、cycle_count
   - `start()`: load_mapping → load_rules → write_mode('coordinator') → 启动 daemon 控制线程
   - `stop()`: running=False → write_mode('standalone') → write_reset_all_outputs()
   - `pause()` / `resume()`: 设置/清除 paused 标志
   - `_control_loop()`: while running → paused 时 sleep → lock 内执行 _execute_cycle → sleep 100ms → 异常不中断
   - `_execute_cycle()`: 完整的 6 步控制流程图：

```
cycle_count += 1

Step 1: check_alive(ALIVE_TIMEOUT)
  ├── 任一离线 → log AUTO_STOP → running=False → return
  └── 在线 → 继续

Step 2: read_voltage_data() + read_ntc_state() + read_thresholds()
  └── 任一为 None → return(跳过本轮)

Step 3: 遍历 self.data_reader.mapping 中每个 (ntc_ch → v_ch)
  │
  ├── actual_v = voltages[v_ch-1]
  ├── expected_v = calc_expected_voltage(...)
  ├── deviation = actual_v - expected_v
  ├── tol = self.tolerances[ntc_ch]
  │
  ├── status = deviation_checker.check(deviation, tol)
  │   ├── 'hw_fault' → log HW_FAULT
  │   ├── 'compensate' + compensation_enabled → calc补偿 → apply → log COMPENSATION
  │   └── 'normal' → 无动作
  │
  ├── if actual_v > thresholds[v_ch-1]:
  │   ├── slope_guard.reduce(ntc_ch, slopes[ch_idx])
  │   ├── link_trigger.on_over_threshold(...)
  │   ├── log SLOPE_ADJ
  │   └── limit_flag = True
  │   else:
  │   ├── slope_guard.try_restore(...)
  │   └── if check_re_reduce → log SLOPE_ADJ_ESCALATE
  │
  ├── if auto_run[ch_idx]:
  │   └── limit_flag |= ramp_driver.step(...)
  │
  ├── if abs(deviation) <= tol → compensation_counts[ntc_ch] = 0
  │
  └── 构建 state_snapshot[ntc_ch] = {...}

Step 4: logger.write_state_snapshot(state_snapshot)

Step 5: if cycle_count % 50 == 0 → logger.flush()  (≈每5秒)

Step 6: if on_state_changed → on_state_changed(state_snapshot)
```

2. 自测：在 Python 中 `engine = AutoCoordinator()` → `engine.start()` 验证控制线程启动

---

## 阶段四：NTC.py 改造（1 文件，约 1 天）

### Task 4.1: NTC.py 增加联动支持
- **需求追溯**: IR-02(NTC.json 扩展)、IR-03(控制指令通道)、NFR-03.4(独立运行兼容)
- **设计参考**: design.md §5.1~§5.3
- **产出物**: 修改 `NTC.py`
- **依赖**: 阶段三（引擎完成后可实测验证）
- **优先级**: P0
- **预估**: 3h

**实施内容**:
1. 在 `MainWindow.__init__()` 末尾新增：
   - `self.watch_timer = QTimer(self)` → `timeout.connect(self.check_external_commands)` → `start(200)`
   - `self.heartbeat_timer = QTimer(self)` → `timeout.connect(self.update_heartbeat)` → `start(500)`

2. 新增方法 `update_heartbeat()`:
   - 读取 NTC.json → 设置 `_heartbeat = {'timestamp': time.time(), 'pid': os.getpid()}` → 原子写入

3. 新增方法 `check_external_commands()`:
   - 读取 NTC.json → 提取 `_external_command`
   - 遍历各通道指令：
     - `'temp'`: 更新 `self.current_t[ch_idx]` → `_update_ui_from_data(ch_idx)` → `update_output_immediately(ch_idx)`
     - `'slope'`: 更新 UI `sp_slope.setValue(new_slope)` (blockSignals 防递归) → 更新 `self.cfg['slope']`
     - `'auto_run'`: 更新 UI `chk_auto.setChecked(bool)` (blockSignals) → 更新 `self.cfg['auto_run']`
   - 最后清空 `cfg['_external_command'] = {}` → 原子写入

4. 修改 `auto_ramp()`:
   - 在原有逻辑开头添加：检查 NTC.json 顶层 `_mode == 'coordinator'` → 是则 return（跳过自身斜坡）

5. 修改 `save_cfg()`:
   - 替换原有的直接文件写入 → 使用临时文件 + `Path.replace` 原子重命名

6. 验收：
   - 不启动联动引擎 → 直接运行 NTC.py → 所有原功能正常（NFR-03.4）
   - 启动联动引擎后 → NTC.py 心跳字段出现 → 外部指令可执行

---

## 阶段五：UI 界面（4 Tab + 主窗口，约 2-3 天）

### Task 5.1: AutoCoordinatorApp 主窗口
- **需求追溯**: FR-10(启动/停止)、FR-11(存活状态显示)
- **设计参考**: design.md §6.1~§6.2
- **产出物**: `auto_coordinator/app.py`
- **依赖**: Task 3.1(引擎)
- **优先级**: P0
- **预估**: 3h

**实施内容**:
1. 实现 `AutoCoordinatorApp(QMainWindow)`:
   - `state_updated = pyqtSignal(dict)`
   - `__init__()`: 创建 AutoCoordinator → `state_updated.connect(_on_state_snapshot)` → `engine.on_state_changed = state_updated.emit` → init_ui → init_timers → connect_buttons
   - `_connect_buttons()`: start → `_on_start`，pause → `_on_pause`，stop → `_on_stop`
   - `_on_start()`: check_alive → 任一离线时 QMessageBox 提示 → 通过则 engine.start() → 更新按钮状态
   - `_on_pause()`: 切换 paused/恢复 → 更新按钮文字和状态栏
   - `_on_stop()`: engine.stop() → 复位按钮状态和状态栏
   - `_init_ui()`: 工具栏（启动/暂停/停止按钮，初始仅启动可用）+ QTabWidget(4 Tab) + 状态栏(NTC状态/1.py状态/引擎状态)
   - `_init_timers()`: 存活检测 QTimer 2 秒周期
   - `_check_alive()`: 更新状态栏文字（●在线 / ○离线）
   - `_on_state_snapshot(state)` (slot): 主线程安全更新 dashboard_tab
2. 添加 `if __name__ == '__main__'` 入口

---

### Task 5.2: 通道绑定 Tab
- **需求追溯**: FR-01(通道绑定管理)、FR-07(配置导出/导入)
- **设计参考**: design.md §6.3
- **产出物**: `auto_coordinator/widgets/bind_tab.py`
- **依赖**: Task 2.1(DataReader)
- **优先级**: P1
- **预估**: 3h

**实施内容**:
1. 实现 `ChannelBindWidget(QWidget)`:
   - 6 行 × 4 列表格（NTC通道/绑定1.py通道/容差(V)/自动补偿）
   - NTC通道列：只读 QLabel "CH1"~"CH6"
   - 绑定通道列：QComboBox (1-18)，从 `channel_mapping` 加载默认值，去重约束（选过的不可再选）
   - 容差列：QDoubleSpinBox (0.01~1.0, 步进 0.01)
   - 自动补偿列：QCheckBox
   - 底部按钮：[保存配置] [导出模板] [导入模板]
   - 保存：批量 UPDATE `channel_mapping` 表 → 调用 `data_reader.load_mapping()` 刷新
   - 导出：收集当前 6 行数据 → 保存为独立 JSON 文件
   - 导入：读取 JSON 文件 → 填充表格 + 保存到 DB

---

### Task 5.3: 联动规则 Tab
- **需求追溯**: FR-05(通道联动)
- **设计参考**: design.md §6.4
- **产出物**: `auto_coordinator/widgets/rules_tab.py`
- **依赖**: 无
- **优先级**: P1
- **预估**: 2h

**实施内容**:
1. 实现 `LinkRulesWidget(QWidget)`:
   - 动态行表格（触发通道 QComboBox / 联动通道 QComboBox / 降斜率系数 QDoubleSpinBox）
   - [添加规则] 按钮 → 新增一行
   - 每行可删除
   - [保存] 按钮 → DELETE ALL + 批量 INSERT `link_rules` 表
   - 初始化时从 `link_rules` 加载已有规则
   - 约束：触发通道 ≠ 联动通道，同一 (trigger, linked) 对不可重复

---

### Task 5.4: 状态看板 Tab
- **需求追溯**: FR-08(联动状态看板)、NFR-01.3(≥5Hz 刷新)
- **设计参考**: design.md §6.5
- **产出物**: `auto_coordinator/widgets/dashboard_tab.py`
- **依赖**: Task 5.1(信号机制)
- **优先级**: P1
- **预估**: 3h

**实施内容**:
1. 实现 `StatusDashboard(QWidget)`:
   - QTableWidget 6 行 × 9 列：通道名/设温(°C)/期望V/实际V/偏差V/补偿V/斜率/限幅/自动
   - `update_from_snapshot(state)`: blockSignals 方式更新所有单元格
   - 行背景色分级：白色(偏差≤容差) / 黄色(容差<偏差≤0.3V) / 橙色(0.3V<偏差≤0.5V) / 红色(偏差>0.5V)
   - 限幅列：限幅时显示"限幅"文字
   - 支持双击容差/补偿开关单元格进行直接修改 → 写回 DB

---

### Task 5.5: 事件日志 Tab
- **需求追溯**: FR-09(事件日志查询/筛选/导出)
- **设计参考**: design.md §6.6
- **产出物**: `auto_coordinator/widgets/log_tab.py`
- **依赖**: 无
- **优先级**: P1
- **预估**: 2.5h

**实施内容**:
1. 实现 `LogPanel(QWidget)`:
   - 顶部筛选栏：事件类型 QComboBox / 通道 QComboBox / 时间范围 QDateTimeEdit × 2 / [查询] [导出Excel]
   - QTableWidget：时间 | 事件类型 | 通道 | 描述
   - `refresh()`: 从 `link_events` 表查询 → 填充表格（用定时器 500ms 刷新）
   - [查询]: 构建 SQL WHERE 条件 → 刷新
   - [导出Excel]: 使用 pandas → `to_excel()`
   - [清除日志]: DELETE FROM link_events → 刷新

---

## 阶段六：测试场景模板与工具（约 1 天）

### Task 6.1: 预置测试场景模板
- **需求追溯**: FR-07.4(预置模板)
- **设计参考**: design.md §8.2
- **产出物**: `auto_coordinator/scenes/` 下 3 个 JSON 文件
- **依赖**: 无
- **优先级**: P2
- **预估**: 1h

**实施内容**:
1. 创建 `冰箱全温验证.json`: CH1(CH11) 0~5°C +1°C/min, CH2(CH12) -18~-12°C -2°C/min, CH3(CH13) 10~40°C +3°C/min，联动 CH1→CH2(系数0.4)
2. 创建 `空调快速测试.json`: 简化版 2 通道配置
3. 创建 `洗碗机标准测试.json`: 简化版 2 通道配置

---

### Task 6.2: 一键启动脚本
- **需求追溯**: FR-10.1(一键启动)
- **设计参考**: design.md §11 run_coordinator.bat
- **产出物**: `run_coordinator.bat`
- **依赖**: Task 5.1(主窗口)
- **优先级**: P2
- **预估**: 0.3h

**实施内容**:
1. 创建 Windows batch 脚本：`python -m auto_coordinator.app`

---

## 阶段七：测试用例（约 2 天）

### Task 7.1: 单元测试
- **需求追溯**: NFR-04.3(代码覆盖率≥60%)
- **设计参考**: design.md §11 tests/ 目录
- **产出物**: `tests/` 下 5 个测试文件
- **依赖**: 阶段二全部
- **优先级**: P2
- **预估**: 5h

**实施内容**:
1. `test_deviation_checker.py`: 覆盖 normal/compensate/hw_fault 三种判定
2. `test_compensator.py`: 覆盖补偿计算、钳位、重试上限
3. `test_slope_guard.py`: 覆盖降斜/恢复/再次降斜场景
4. `test_data_reader.py`: 覆盖读电压/读 NTC 状态/存活检测（可用 mock 数据）
5. `test_engine.py`: 覆盖启动/暂停/停止状态转换、控制循环基本流程

---

### Task 7.2: 集成测试文档
- **需求追溯**: §8.2（集成验收测试场景）
- **设计参考**: requirement.md §8.2 三个场景
- **产出物**: `tests/integration_test_plan.md`
- **依赖**: 阶段三~五全部
- **优先级**: P2
- **预估**: 1h

**实施内容**:
1. 编写场景一（冰箱标准测试）操作步骤和验收检查点
2. 编写场景二（异常模拟）操作步骤和验收检查点
3. 编写场景三（24h 稳定性）操作步骤和验收检查点

---

## 任务依赖关系图

```
阶段一 (基础设施)
  Task 1.1 (migration.sql) ──┐
  Task 1.2 (config.json)   ──┤
                              │
阶段二 (核心模块)              │
  Task 2.1 (DataReader)     ←┘
  Task 2.2 (NTCJsonWriter)   (无依赖)
  Task 2.3 (DeviationChecker)(无依赖)
  Task 2.4 (Compensator)    ← Task 2.1, 2.2
  Task 2.5 (SlopeGuard)     ← Task 2.2
  Task 2.6 (LinkTrigger)    ← Task 2.5
  Task 2.7 (RampDriver)     ← Task 2.2
  Task 2.8 (LinkLogger)      (无依赖)
                              │
阶段三 (引擎组装)              │
  Task 3.1 (engine.py)      ← Task 2.1~2.8 全部
                              │
阶段四 (NTC.py 改造)          │
  Task 4.1 (NTC.py)         ← Task 3.1 (可并行)
                              │
阶段五 (UI)                   │
  Task 5.1 (app.py)         ← Task 3.1
  Task 5.2 (bind_tab.py)    ← Task 2.1
  Task 5.3 (rules_tab.py)    (无依赖)
  Task 5.4 (dashboard_tab)  ← Task 5.1
  Task 5.5 (log_tab.py)      (无依赖)
                              │
阶段六 (工具)                 │
  Task 6.1 (scenes/)         (无依赖)
  Task 6.2 (run bat)        ← Task 5.1
                              │
阶段七 (测试)                 │
  Task 7.1 (单元测试)        ← 阶段二全部
  Task 7.2 (集成测试文档)    ← 阶段五全部
```

---

## 工作量汇总

| 阶段 | 任务数 | 预估总时 | 优先级分布 |
|------|--------|----------|------------|
| 阶段一 基础设施 | 2 | 0.8h | P0×2 |
| 阶段二 核心模块 | 8 | 16.5h | P0×6, P1×2 |
| 阶段三 引擎组装 | 1 | 3h | P0×1 |
| 阶段四 NTC.py 改造 | 1 | 3h | P0×1 |
| 阶段五 UI 界面 | 5 | 13.5h | P0×1, P1×4 |
| 阶段六 工具 | 2 | 1.3h | P2×2 |
| 阶段七 测试 | 2 | 6h | P2×2 |
| **合计** | **21** | **~44h** | P0:10 / P1:6 / P2:5 |

---

## 交付物总清单

```
jiaohu/
├── auto_coordinator/
│   ├── __init__.py
│   ├── config.json                          ← Task 1.2
│   ├── migration_v1.sql                     ← Task 1.1
│   ├── engine.py                            ← Task 3.1
│   ├── data_reader.py                       ← Task 2.1
│   ├── ntc_json_writer.py                   ← Task 2.2
│   ├── deviation_checker.py                 ← Task 2.3
│   ├── compensator.py                       ← Task 2.4
│   ├── slope_guard.py                       ← Task 2.5
│   ├── link_trigger.py                      ← Task 2.6
│   ├── ramp_driver.py                       ← Task 2.7
│   ├── link_logger.py                       ← Task 2.8
│   ├── app.py                               ← Task 5.1
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── bind_tab.py                      ← Task 5.2
│   │   ├── rules_tab.py                     ← Task 5.3
│   │   ├── dashboard_tab.py                 ← Task 5.4
│   │   └── log_tab.py                       ← Task 5.5
│   └── scenes/
│       ├── 冰箱全温验证.json                 ← Task 6.1
│       ├── 空调快速测试.json                 ← Task 6.1
│       └── 洗碗机标准测试.json               ← Task 6.1
├── NTC.py                                   ← Task 4.1 (修改)
├── run_coordinator.bat                      ← Task 6.2
└── tests/
    ├── test_deviation_checker.py            ← Task 7.1
    ├── test_compensator.py                  ← Task 7.1
    ├── test_slope_guard.py                  ← Task 7.1
    ├── test_data_reader.py                  ← Task 7.1
    ├── test_engine.py                       ← Task 7.1
    └── integration_test_plan.md             ← Task 7.2
```

<br>

_文档结束_
