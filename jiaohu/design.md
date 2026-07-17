# 工装测试上位机自动化联动设计文档

---

## 版本记录

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|----------|
| v1.0 | 2026-07-16 | — | 初始版本，基于 requirement.md v1.0 |
| v1.1 | 2026-07-16 | — | 修复评审发现的 13 个设计问题：新增 NTCJsonWriter、补齐 SlopeGuard、修正竞态/补偿/模式字段/联动规则加载/输出复位/线程安全等 |

---

## 1. 设计概述

### 1.1 设计目标

基于 `requirement.md` 定义的需求，设计一套联动调度引擎，桥接 1.py（18路电压采集）和 NTC.py（6通道温度模拟），实现温度-电压闭环自动控制。

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| **最小侵入** | 对现有 1.py 和 NTC.py 的改动最小化，保持各自独立运行能力 |
| **松耦合** | 联动引擎通过文件（DB/JSON）与两个上位机通信，不引入新的 IPC 框架 |
| **核心与 UI 分离** | 引擎逻辑独立于 UI，可单独测试、单独运行 |
| **安全钳位** | 所有对外输出经过多级安全校验（电压≤5V、温度∈[-50,150]℃、斜率≥0） |
| **可配置** | 关键参数集中管理，支持运行时调整 |

### 1.3 参考文档

| 文档 | 路径 |
|------|------|
| 需求文档 | `requirement.md` |
| 联动方案 | `自动化联动方案.md` |
| 现有代码 | `1.py` (1767行), `NTC.py` (749行) |
| 项目说明 | `README.md` |

---

## 2. 系统架构设计

### 2.1 架构总览

采用"独立 GUI 进程 + 嵌入式引擎"模式：**联动引擎嵌入一个新的 PyQt5 主窗口进程，作为协调中心**，同时 1.py 和 NTC.py 各自保持独立进程运行。

```
进程 C: AutoCoordinatorApp (新增 PyQt5 GUI，内含 AutoCoordinator 引擎)
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  ┌────────────────────┐  ┌──────────────────┐  ┌────────────────┐  │
│  │  ChannelBindWidget │  │  StatusDashboard  │  │  LogPanel      │  │
│  │  (通道绑定配置页)   │  │  (联动状态看板)    │  │  (事件日志)    │  │
│  └────────┬───────────┘  └────────┬─────────┘  └───────┬────────┘  │
│           │                       │                     │           │
│           └───────────┬───────────┴─────────────────────┘           │
│                       ▼                                              │
│           ┌─────────────────────────────┐                           │
│           │     AutoCoordinator         │                           │
│           │  (联动引擎核心，独立线程)     │                           │
│           │                             │                           │
│           │  控制循环 (100ms)            │                           │
│           │  1. DataReader 读两端状态    │                           │
│           │  2. DeviationChecker 算偏差   │                           │
│           │  3. Compensator 自动补偿     │                           │
│           │  4. SlopeGuard 超限降斜率    │                           │
│           │  5. LinkTrigger 通道联动     │                           │
│           │  6. RampDriver 斜坡推进      │                           │
│           │  7. Logger 写日志            │                           │
│           └──────────────┬──────────────┘                           │
│                          │                                           │
│         读 ──────────────┼───────────── 写                           │
│         ▼                │                ▼                          │
│   DB / JSON 共享层        │          NTC.json                       │
│                          │         (_external_command               │
│                          │          + _heartbeat)                   │
└──────────────────────────┼──────────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
     voltage_data.db   config.json      NTC.json
          ▲                               │ 轮询
          │ 写入                           ▼
     ┌────┴──────┐              ┌──────────────────┐
     │   1.py    │              │     NTC.py       │
     │ (采集进程) │              │   (模拟输出进程)  │
     └───────────┘              └──────────────────┘
```

### 2.2 组件关系图 (UML 风格)

```
┌──────────────────┐          ┌──────────────────┐
│  AutoCoordinator │          │  DataReader       │
│  - running:bool  │◇────────│  +read_voltage_db()│
│  - mapping:dict  │          │  +read_ntc_json() │
│  - tolerances:dict│         │  +read_config()   │
│  +start()        │          │  +calc_expected_v()│
│  +stop()         │          └──────────────────┘
│  +control_loop() │                   │
└────────┬─────────┘                   │ 使用
         │                             ▼
         │                   ┌──────────────────┐
         │                   │  NTCConverter    │
         │ 包含              │  (复用现有逻辑)    │
         │                   │  +temp2voltage()  │
    ┌────┴────────────┐      │  +voltage2temp()  │
    │                 │      │  (新增逆查表)     │
    ▼                 ▼      └──────────────────┘
┌──────────┐  ┌──────────┐
│DevChecker│  │RampDriver│
│+check()  │  │+step()   │
│+classify │  │+clip()   │
└────┬─────┘  └────┬─────┘
     │              │
     ▼              ▼
┌──────────┐  ┌──────────┐
│Compensator│ │SlopeGuard │
│+calc()   │  │+reduce() │
│+apply()  │  │+restore()│
└──────────┘  └────┬─────┘
                    │ 调用
                    ▼
              ┌──────────┐
              │LinkTrigger│
              │+execute() │
              └──────────┘
```

### 2.3 线程模型

```
┌───────────────────────────────────────────────────┐
│                  MainWindow (PyQt5 主线程)          │
│                                                   │
│  ┌─────────────────────────┐                      │
│  │  ChannelBindWidget      │ ← 用户事件驱动       │
│  │  StatusDashboard        │ ← QTimer 200ms 刷新  │
│  │  LogPanel               │ ← QTimer 500ms 刷新  │
│  └─────────────────────────┘                      │
│                       │                           │
│                       │ 通过 thread-safe 队列     │
│                       ▼                           │
│  ┌─────────────────────────────────────────────┐  │
│  │  AutoCoordinator 控制线程                     │  │
│  │  (独立 Python Thread, daemon=True)           │  │
│  │                                             │  │
│  │  while self.running:                        │  │
│  │      with self.lock:                        │  │
│  │          各检查器依次执行                     │  │
│  │      sleep(100ms)                            │  │
│  └─────────────────────────────────────────────┘  │
│                                                   │
│  ┌─────────────────────────────────────────────┐  │
│  │  DB Write 线程 (后台)                         │  │
│  │  异步批量写入 link_run_state                  │  │
│  └─────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────┘
```

> **决策理由**：采用独立线程而非独立进程，因为：
> 1. 联动引擎需要读取 NTC 曲线数据（内存中的 `curves` dict），进程中读文件即可
> 2. 状态看板需要高频刷新（5Hz），如果跨进程则需要额外序列化开销
> 3. 约束 C-02 允许最终设计阶段决定部署形式

---

## 3. 核心模块详细设计

### 3.1 AutoCoordinator（联动引擎核心）

**职责**：调度整个闭环控制循环，协调各检查器/执行器的执行顺序。

**文件**：`auto_coordinator/engine.py`

```python
class AutoCoordinator:
    """
    联动调度引擎
    """

    # ── 配置 ──
    CONTROL_INTERVAL = 0.1        # 控制周期 100ms
    MAX_DEVIATION_HW = 0.5        # 硬件故障判定阈值 (V)
    COMPENSATION_DAMPING = 0.5    # 补偿阻尼系数
    COMPENSATION_MAX = 0.2        # 单次补偿上限 (V)
    COMPENSATION_RETRY_MAX = 10   # 最大补偿重试次数
    SLOPE_REDUCE_DEFAULT = 0.5    # 默认降压系数
    SLOPE_RESTORE_CYCLES = 5      # 恢复检查周期数
    ALIVE_TIMEOUT = 10.0          # 心跳超时 (秒)

    def __init__(self):
        # 状态
        self.running = False
        self.paused = False
        self.lock = threading.Lock()

        # 共享写入器（解决竞态条件）
        self.ntc_writer = NTCJsonWriter(
            Path(__file__).parent.parent / 'NTC.json'
        )

        # 子模块
        self.data_reader = DataReader()
        self.deviation_checker = DeviationChecker()
        self.compensator = Compensator(self.data_reader, self.ntc_writer)
        self.slope_guard = SlopeGuard(self.data_reader, self.ntc_writer)
        self.link_trigger = LinkTrigger(self.slope_guard)
        self.ramp_driver = RampDriver(self.data_reader, self.ntc_writer)
        self.logger = LinkLogger(self.data_reader.db_path)

        # 每通道补偿计数
        self.compensation_counts = {i: 0 for i in range(1, 7)}
        self.cycle_count = 0

        # 回调
        self.on_state_changed = None   # Callable[[dict], None] — UI 回调

    def start(self):
        """启动控制循环"""
        self.data_reader.load_mapping()
        self.link_trigger.load_rules(self.data_reader.db_path)
        self.ntc_writer.write_mode('coordinator')
        self.running = True
        thread = threading.Thread(target=self._control_loop, daemon=True)
        thread.start()

    def stop(self):
        """停止控制循环，复位输出"""
        self.running = False
        self.ntc_writer.write_mode('standalone')
        self.ntc_writer.write_reset_all_outputs()

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def _control_loop(self):
        while self.running:
            if self.paused:
                time.sleep(0.1)
                continue

            with self.lock:
                try:
                    self._execute_cycle()
                except Exception as e:
                    logging.error(f"控制循环异常: {e}", exc_info=True)

            time.sleep(self.CONTROL_INTERVAL)

    def _execute_cycle(self):
        """单周期执行"""
        self.cycle_count += 1

        # 1. 检查上位机存活
        alive = self.data_reader.check_alive(self.ALIVE_TIMEOUT)
        if not alive['ntc'] or not alive['voltage']:
            self.logger.log('AUTO_STOP', None, '上位机离线',
                {'ntc': alive['ntc'], 'voltage': alive['voltage']})
            self.running = False
            return

        # 2. 读取数据
        voltages = self.data_reader.read_voltage_data()
        ntc_state = self.data_reader.read_ntc_state()
        thresholds = self.data_reader.read_thresholds()
        if voltages is None or ntc_state is None:
            return

        # 3. 逐通道处理
        state_snapshot = {}
        for ntc_ch, v_ch in self.data_reader.mapping.items():
            ch_idx = ntc_ch - 1

            actual_v = voltages[v_ch - 1]
            expected_v = self.data_reader.calc_expected_voltage(
                ntc_state['temps'][ch_idx],
                ntc_state['curves'][ch_idx],
                ntc_state['pullup_resistors'][ch_idx]
            )
            deviation = actual_v - expected_v
            tol = self.data_reader.tolerances.get(ntc_ch, 0.05)

            # 偏差检测
            status = self.deviation_checker.check(deviation, tol)
            compensation = 0.0
            limit_flag = False

            if status == 'hw_fault':
                self.logger.log('HW_FAULT', ntc_ch,
                    f'大偏差: {deviation:.3f}V', {'deviation': deviation})
            elif status == 'compensate':
                if self.data_reader.compensation_enabled.get(ntc_ch, True):
                    compensation = self.compensator.calc(
                        ntc_ch, deviation, self.compensation_counts)
                    if compensation is not None:
                        self.compensator.apply(ntc_ch, compensation)
                        self.logger.log('COMPENSATION', ntc_ch,
                            f'补偿 {compensation:.3f}V',
                            {'deviation': deviation, 'compensation': compensation})
                        limit_flag = self.compensator.is_compensation_limit(
                            ntc_ch, self.compensation_counts)
                        self.compensation_counts[ntc_ch] += 1
                else:
                    self.compensation_counts[ntc_ch] = 0

            # 超阈值检查
            if actual_v > thresholds[v_ch - 1]:
                self.slope_guard.reduce(ntc_ch, ntc_state['slopes'][ch_idx])
                self.link_trigger.on_over_threshold(ntc_ch, actual_v,
                    thresholds, self.data_reader.mapping)
                limit_flag = True
                self.logger.log('SLOPE_ADJ', ntc_ch,
                    f'超阈值: {actual_v:.3f}V > {thresholds[v_ch-1]:.3f}V')
            else:
                self.slope_guard.try_restore(ntc_ch, actual_v, thresholds[v_ch - 1])
                # FR-04.4: 降斜率后回读仍上升 → 再次降斜 + 高级告警
                if self.slope_guard.check_re_reduce(ntc_ch, actual_v):
                    self.logger.log('SLOPE_ADJ_ESCALATE', ntc_ch,
                        f'降斜率后仍持续上升，再次降斜率')

            # 自动斜坡
            if ntc_state['auto_run'][ch_idx]:
                limit_flag = self.ramp_driver.step(ntc_ch, ntc_state, ch_idx) or limit_flag

            # 复位计数（如果偏差回归正常）
            if abs(deviation) <= tol:
                self.compensation_counts[ntc_ch] = 0

            # 构建快照
            state_snapshot[ntc_ch] = {
                'target_temp': ntc_state['temps'][ch_idx],
                'target_voltage': expected_v,
                'actual_voltage': actual_v,
                'deviation': deviation,
                'tolerance': tol,
                'compensation': compensation,
                'slope': ntc_state['slopes'][ch_idx],
                'auto_run': ntc_state['auto_run'][ch_idx],
                'is_limited': limit_flag,
                'channel_name': ntc_state.get('ch_names', [f'CH{i}' for i in range(1,7)])[ch_idx],
                'status': status,
            }

        # 4. 持久化运行状态
        self.logger.write_state_snapshot(state_snapshot)

        # 5. 批量写入 DB（每 50 周期 ≈ 5 秒）
        if self.cycle_count % 50 == 0:
            self.logger.flush()

        # 6. UI 回调（通过信号发送到主线程，见 6.1）
        if self.on_state_changed:
            self.on_state_changed(state_snapshot)
```

### 3.2 DataReader（数据读取与状态感知）

**职责**：封装所有对外部数据源的读取操作，提供统一的数据获取接口。

**文件**：`auto_coordinator/data_reader.py`

```python
class DataReader:
    """统一数据读取层"""

    def __init__(self):
        self.BASE_DIR = Path(__file__).parent.parent  # jiaohu 目录
        self.db_path = str(self.BASE_DIR / 'voltage_data.db')
        self.config_path = str(self.BASE_DIR / 'config.json')
        self.ntc_json_path = self.BASE_DIR / 'NTC.json'
        self.ntc_xlsx_path = self.BASE_DIR / 'NTC.xlsx'

        # 映射
        self.mapping = {}
        self.tolerances = {}
        self.compensation_enabled = {}

        # NTC 曲线缓存（初始化时加载）
        self.curves = {}
        self._load_curves()

    def _load_curves(self):
        """加载 NTC 曲线数据"""
        from NTC import load_ntc_curves_from_excel
        self.curves = load_ntc_curves_from_excel(self.ntc_xlsx_path)
        if not self.curves:
            raise RuntimeError("无法加载NTC曲线数据")

    def load_mapping(self):
        """从 DB 加载通道绑定"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ntc_channel, voltage_channel, tolerance, "
            "compensation_enabled FROM channel_mapping"
        )
        for row in cursor.fetchall():
            ntc_ch, v_ch, tol, comp = row
            self.mapping[ntc_ch] = v_ch
            self.tolerances[ntc_ch] = tol
            self.compensation_enabled[ntc_ch] = bool(comp)
        conn.close()

    def read_voltage_data(self) -> Optional[List[float]]:
        """读取 1.py 最新电压数据 (18通道)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT channel1,channel2,channel3,channel4,channel5,channel6,"
            "channel7,channel8,channel9,channel10,channel11,channel12,"
            "channel13,channel14,channel15,channel16,channel17,channel18,"
            "timestamp FROM voltage_data ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            voltages = list(row[:-1])  # 前18个是电压
            return voltages
        return None

    def read_ntc_state(self) -> Optional[dict]:
        """
        读取 NTC.py 当前状态。
        采用"读两遍比较"策略保证一致性，避免读到 NTC.py 写入中途的半完成数据。
        若两次不一致，再读第三次；若第三次与第二次一致则采用，否则放弃本轮。
        设计权衡：若 NTC.py 心跳 500ms 写一次，联动引擎 100ms 读一次，平均每 5 次
        读有 1 次读到写入中途 → 回退重试即可，极少情况下连续不一致导致 None。
        """
        raw1 = self._read_json_safe(self.ntc_json_path)
        raw2 = self._read_json_safe(self.ntc_json_path)
        if raw1 != raw2:
            raw3 = self._read_json_safe(self.ntc_json_path)
            if raw2 != raw3:
                return None  # 连续不一致，放弃本次
        cfg = raw2 or {}
        return {
            'temps': cfg.get('ntc_temps', [25]*6),
            'curves': cfg.get('ntc_curves', ['10k']*6),
            'slopes': cfg.get('slope', [5.0]*6),
            'auto_run': cfg.get('auto_run', [False]*6),
            'limit_high': cfg.get('limit_temp_high', [100]*6),
            'limit_low': cfg.get('limit_temp_low', [-50]*6),
            'pullup_resistors': cfg.get('pullup_resistors', [10.0]*6),
            'ch_names': cfg.get('ch_names', [f'CH{i}' for i in range(1, 7)]),
            'heartbeat': cfg.get('_heartbeat', {}),
        }

    def read_thresholds(self) -> List[float]:
        """读取 config.json 中的 18 路阈值"""
        cfg = self._read_json_safe(self.config_path)
        if cfg:
            return cfg.get('thresholds', [5.0]*18)
        return [5.0]*18

    def calc_expected_voltage(self, temp: float, curve_name: str,
                              pullup: float) -> float:
        """计算期望电压"""
        if curve_name not in self.curves:
            return 0.0
        conv = NTCConverter(self.curves[curve_name], pullup)
        return conv.temp2voltage(temp)

    def check_alive(self, timeout: float) -> dict:
        """检查两个上位机存活"""
        result = {'ntc': False, 'voltage': False}

        # NTC.py: 检查 _heartbeat
        try:
            cfg = self._read_json_safe(self.ntc_json_path)
            if cfg:
                hb = cfg.get('_heartbeat', {})
                ts = hb.get('timestamp', 0)
                if time.time() - ts < timeout:
                    result['ntc'] = True
        except:
            pass

        # 1.py: 检查 voltage_data 最新记录时间戳
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT timestamp FROM voltage_data ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            conn.close()
            if row:
                ts_str = row[0]
                ts = datetime.datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S.%f').timestamp()
                if time.time() - ts < timeout:
                    result['voltage'] = True
        except:
            pass

        return result

    def _read_json_safe(self, path) -> Optional[dict]:
        """安全读取 JSON，不抛异常"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return None


class NTCConverter:
    """
    复用 NTC.py 的 NTCConverter 逻辑。
    注意：为避免循环导入，在此复制核心实现 + 扩展逆查表。
    """

    def __init__(self, k_table: dict, pullup_resistor_kohm: float = 10.0):
        if not k_table:
            raise ValueError("NTC曲线数据表不能为空")
        self.table = k_table
        self.temps = sorted(self.table.keys())
        self.pullup_resistor_kohm = pullup_resistor_kohm
        # 预计算 -50~150 的正向电压缓存
        self.voltage_cache = [self._calc_voltage(t) for t in range(-50, 151)]
        # 预计算逆向查表：电压(10mV步进) -> 最近温度
        self._build_inverse_cache()

    def _calc_voltage(self, t: int) -> float:
        """与 NTC.py 逻辑一致，返回 0-5V"""
        if t <= self.temps[0]:
            r_k = self.table[self.temps[0]]
        elif t >= self.temps[-1]:
            r_k = self.table[self.temps[-1]]
        else:
            for i in range(len(self.temps) - 1):
                t1, t2 = self.temps[i], self.temps[i + 1]
                if t1 <= t <= t2:
                    r_k = self.table[t1] + (self.table[t2] - self.table[t1]) * (t - t1) / (t2 - t1)
                    break
            else:
                r_k = self.table[self.temps[-1]]
        r = max(0.001, r_k * 1000)
        pullup_r = self.pullup_resistor_kohm * 1000
        return max(0.0, min(5.0, 5.0 * r / (r + pullup_r)))

    def temp2voltage(self, t: float) -> float:
        """温度→电压，O(1) 查表"""
        t_int = int(round(t))
        idx = max(-50, min(150, t_int)) + 50
        return self.voltage_cache[idx]

    def _build_inverse_cache(self):
        """构建逆向查表：电压(10mV步进 0~5V) -> 最近温度"""
        self.inverse_cache = []
        for mv in range(0, 5001, 10):  # 0mV~5000mV, 步进10mV
            target_v = mv / 1000.0
            best_temp = -50
            best_diff = float('inf')
            for t in range(-50, 151):
                diff = abs(self.voltage_cache[t + 50] - target_v)
                if diff < best_diff:
                    best_diff = diff
                    best_temp = t
            self.inverse_cache.append(best_temp)

    def voltage2temp_approx(self, v: float) -> float:
        """电压→温度近似查表，O(1)，用于补偿计算"""
        mv = max(0, min(5000, int(round(v * 1000))))
        idx = mv // 10
        return float(self.inverse_cache[min(idx, 500)])
```

### 3.2.1 NTCJsonWriter（NTC.json 写入器）

**职责**：封装对 NTC.json 的所有写操作，提供合并写入 + 原子重命名，消除多模块并发写入的竞态条件。

**文件**：`auto_coordinator/ntc_json_writer.py`

```python
class NTCJsonWriter:
    """NTC.json 原子写入器，串联所有写 NTC.json 的模块"""

    def __init__(self, ntc_json_path: Path):
        self.path = ntc_json_path
        self.tmp_path = ntc_json_path.with_suffix('.tmp')
        self._lock = threading.Lock()

    def write_cmd(self, ntc_ch: int, **kwargs):
        """
        写入单个通道的控制指令（temp, slope, auto_run 等）。
        自动合并到已有的 _external_command 中，不覆盖其他通道的指令（仅本轮）。
        线程安全，使用内部锁防止并发写入。
        """
        with self._lock:
            cfg = self._read_cfg()
            if '_external_command' not in cfg:
                cfg['_external_command'] = {}
            ch_key = str(ntc_ch - 1)
            if ch_key not in cfg['_external_command']:
                cfg['_external_command'][ch_key] = {}
            cfg['_external_command'][ch_key].update(kwargs)
            self._write_cfg(cfg)

    def write_mode(self, mode: str):
        """写入联动模式标志（'coordinator' 或 'standalone'），存入 NTC.json 顶层"""
        with self._lock:
            cfg = self._read_cfg()
            cfg['_mode'] = mode
            self._write_cfg(cfg)

    def write_reset_all_outputs(self):
        """停止时复位：温度归初始值(25°C)，斜率归0，auto_run 关闭"""
        with self._lock:
            cfg = self._read_cfg()
            cfg['_external_command'] = {}
            for ch_idx in range(6):
                cfg['_external_command'][str(ch_idx)] = {
                    'temp': 25.0, 'slope': 0.0, 'auto_run': False
                }
            self._write_cfg(cfg)

    def clear_commands(self):
        """清除本轮所有 _external_command（由 NTC.py 执行后调用；引擎侧极少使用）"""
        with self._lock:
            cfg = self._read_cfg()
            cfg['_external_command'] = {}
            self._write_cfg(cfg)

    def _read_cfg(self) -> dict:
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}

    def _write_cfg(self, cfg: dict):
        with open(self.tmp_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        self.tmp_path.replace(self.path)  # 原子重命名
```

**集成说明**：
- AutoCoordinator 在 `__init__` 中创建单个 `NTCJsonWriter` 实例，注入 `Compensator`、`RampDriver`、`SlopeGuard`
- 所有模块通过同一个 writer 写入，内部锁确保同一控制周期内多次写不会互相覆盖
- 每一轮控制循环结束后 `_external_command` 被 NTC.py 清空，下轮重新写入

---

### 3.3 DeviationChecker（偏差检测与分级）

**职责**：将偏差值与容差比较，判定所属级别。

**文件**：`auto_coordinator/deviation_checker.py`

```python
class DeviationChecker:
    """偏差分级判定器"""

    HW_FAULT_THRESHOLD = 0.5  # (V) — 从 AutoCoordinator.MAX_DEVIATION_HW

    @staticmethod
    def check(deviation: float, tolerance: float) -> str:
        """
        返回值:
            'normal'     — 偏差在容差内
            'compensate' — 需要自动补偿
            'hw_fault'   — 硬件故障级别
        """
        abs_dev = abs(deviation)
        if abs_dev <= tolerance:
            return 'normal'
        if abs_dev > DeviationChecker.HW_FAULT_THRESHOLD:
            return 'hw_fault'
        return 'compensate'
```

### 3.4 Compensator（自动补偿）

**职责**：计算补偿量并下发到 NTC.py。

**文件**：`auto_coordinator/compensator.py`

```python
class Compensator:
    """自动补偿器"""

    DAMPING = 0.5       # 阻尼系数
    MAX_PER_CYCLE = 0.2 # 单周期最大补偿 (V)
    MAX_RETRY = 10      # 最大重试次数

    def __init__(self, data_reader, ntc_writer):
        self.data_reader = data_reader
        self.ntc_writer = ntc_writer

    def calc(self, ntc_ch: int, deviation: float,
             compensation_counts: dict) -> Optional[float]:
        """
        计算补偿电压量
        返回 None 表示不需要补偿（已达上限）
        """
        if compensation_counts.get(ntc_ch, 0) >= self.MAX_RETRY:
            return None

        comp_v = -deviation * self.DAMPING
        comp_v = max(-self.MAX_PER_CYCLE, min(self.MAX_PER_CYCLE, comp_v))
        return comp_v

    def apply(self, ntc_ch: int, compensation_v: float):
        """
        将补偿量应用到 NTC.py：
        1. 读取当前温度 → 计算期望电压
        2. 目标电压 = 期望电压 + 补偿量 → 反算目标温度
        3. 通过 NTCJsonWriter.write_cmd 写入
        """
        state = self.data_reader.read_ntc_state()
        if not state:
            return

        ch_idx = ntc_ch - 1
        current_temp = state['temps'][ch_idx]
        current_curve = state['curves'][ch_idx]
        current_pullup = state['pullup_resistors'][ch_idx]

        conv = NTCConverter(
            self.data_reader.curves[current_curve],
            current_pullup
        )
        expected_v = conv.temp2voltage(current_temp)

        target_v = expected_v + compensation_v
        target_v = max(0.0, min(5.0, target_v))

        target_temp = conv.voltage2temp_approx(target_v)
        target_temp = max(-50.0, min(150.0, target_temp))

        self.ntc_writer.write_cmd(ntc_ch, temp=target_temp)

    def is_compensation_limit(self, ntc_ch: int,
                               compensation_counts: dict) -> bool:
        """检查补偿计数是否已达上限"""
        return compensation_counts.get(ntc_ch, 0) >= self.MAX_RETRY
```

### 3.5 SlopeGuard（超阈值斜率卫士）

**职责**：检测回读电压超阈值后自动降低斜率，并在恢复正常后尝试恢复。

**文件**：`auto_coordinator/slope_guard.py`

```python
class SlopeGuard:
    """斜率卫士"""

    REDUCE_FACTOR = 0.5       # 默认降压系数
    RESTORE_CYCLES = 5        # 回落周期后恢复
    MIN_SLOPE = 0.1           # 斜率下限
    RE_REDUCE_THRESHOLD = 3   # 降斜率后仍连续上升N周期 → 再次降斜
    MAX_REDUCE_LEVELS = 2     # 最多降2次（首次0.5→再次0.25）

    def __init__(self, data_reader, ntc_writer):
        self.data_reader = data_reader
        self.ntc_writer = ntc_writer
        # {ntc_ch: {'original_slope': float, 'cycles_recovering': int,
        #            'current_slope': float, 'reduce_level': int,
        #            'last_v': float, 'rising_count': int}}
        self.state = {}

    def reduce(self, ntc_ch: int, current_slope: float, factor: float = None):
        """降低通道斜率"""
        if factor is None:
            factor = self.REDUCE_FACTOR

        if ntc_ch not in self.state:
            self.state[ntc_ch] = {
                'original_slope': current_slope,
                'current_slope': current_slope,
                'cycles_recovering': 0,
                'reduce_level': 0,
                'last_v': 0.0,
                'rising_count': 0,
            }

        st = self.state[ntc_ch]
        if st['reduce_level'] >= self.MAX_REDUCE_LEVELS:
            return  # 已达最大降级次数，不再降低

        new_slope = max(self.MIN_SLOPE, current_slope * factor)
        st['current_slope'] = new_slope
        st['reduce_level'] += 1
        st['rising_count'] = 0

        # 写入 NTC.py
        self.ntc_writer.write_cmd(ntc_ch, slope=new_slope)

    def check_re_reduce(self, ntc_ch: int, actual_v: float) -> bool:
        """
        降斜率后检测回读是否仍持续上升(FR-04.4)。
        返回 True 表示触发了再次降斜率+高级告警。
        """
        if ntc_ch not in self.state:
            return False
        st = self.state[ntc_ch]
        last_v = st['last_v']
        st['last_v'] = actual_v

        if actual_v > last_v:
            st['rising_count'] += 1
            if st['rising_count'] >= self.RE_REDUCE_THRESHOLD:
                if st['reduce_level'] < self.MAX_REDUCE_LEVELS:
                    self.reduce(ntc_ch, st['current_slope'])
                    return True  # 调用者应记录 ESCALATED_ALARM
        else:
            st['rising_count'] = 0
        return False

    def try_restore(self, ntc_ch: int, actual_v: float, threshold: float):
        """尝试恢复斜率"""
        if ntc_ch not in self.state:
            return

        st = self.state[ntc_ch]
        if actual_v <= threshold:
            st['cycles_recovering'] += 1
            if st['cycles_recovering'] >= self.RESTORE_CYCLES:
                # 恢复原始斜率
                self.ntc_writer.write_cmd(ntc_ch, slope=st['original_slope'])
                del self.state[ntc_ch]
        else:
            st['cycles_recovering'] = 0
```

### 3.6 LinkTrigger（通道联动触发器）

**职责**：当触发通道超阈值时，查找联动规则并执行联动通道的斜率调整。

**文件**：`auto_coordinator/link_trigger.py`

```python
class LinkTrigger:
    """通道联动触发器"""

    def __init__(self, slope_guard):
        self.slope_guard = slope_guard
        self._rules_cache = []     # [(trigger_ch, linked_ch, multiplier), ...]

    def load_rules(self, db_path: str):
        """加载联动规则"""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT trigger_channel, linked_channel, slope_multiplier "
            "FROM link_rules"
        )
        self._rules_cache = list(cursor.fetchall())
        conn.close()

    def on_over_threshold(self, trigger_ch: int, actual_v: float,
                          thresholds: list, mapping: dict):
        """
        触发通道超阈值时的联动处理:
        - 加载规则
        - 找到所有联动通道
        - 对每个联动通道降斜率
        """
        for rule in self._rules_cache:
            trig, linked, multiplier = rule
            if trig == trigger_ch:
                current_slope = self.slope_guard.state.get(
                    linked, {}).get('current_slope', 5.0)
                self.slope_guard.reduce(linked, current_slope, multiplier)
                # 日志记录由 engine 处理
```

### 3.7 RampDriver（自动斜坡驱动器）

**职责**：接管 NTC.py 的 `auto_ramp()` 逻辑，在联动引擎侧执行温度推进。

**设计要点**：

- 保留 NTC.py 的 `auto_ramp()` 定时器运行以维持心跳和响应外部指令
- 联动引擎侧的 RampDriver 负责**主控温度推进**，NTC.py 侧仅做**回读执行**（检测 `_external_command` 后应用新温度）
- NTC.py 的 `auto_ramp()` 定时器在检测到联动引擎启动后自动停用自身的斜坡逻辑，转为纯指令执行模式

**文件**：`auto_coordinator/ramp_driver.py`

```python
class RampDriver:
    """自动斜坡驱动器 (联动引擎侧)"""

    MIN_TEMP = -50.0
    MAX_TEMP = 150.0

    def __init__(self, data_reader, ntc_writer):
        self.data_reader = data_reader
        self.ntc_writer = ntc_writer

    def step(self, ntc_ch: int, ntc_state: dict, ch_idx: int) -> bool:
        """
        单步推进，返回是否到达限幅点
        """
        slope = ntc_state['slopes'][ch_idx]
        current_temp = ntc_state['temps'][ch_idx]
        limit_high = ntc_state['limit_high'][ch_idx]
        limit_low = ntc_state['limit_low'][ch_idx]

        delta = (slope / 60.0) * 0.1  # 100ms 是 1/600 分钟
        new_temp = current_temp + delta
        limited = False

        if slope >= 0 and new_temp >= limit_high:
            new_temp = float(limit_high)
            limited = True
        elif slope < 0 and new_temp <= limit_low:
            new_temp = float(limit_low)
            limited = True

        new_temp = max(self.MIN_TEMP, min(self.MAX_TEMP, new_temp))

        self.ntc_writer.write_cmd(ntc_ch, temp=new_temp)

        return limited
```

### 3.8 LinkLogger（联动日志）

**职责**：将联动事件和运行状态写入数据库。

**文件**：`auto_coordinator/link_logger.py`

```python
class LinkLogger:
    """联动日志记录器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._state_queue = deque(maxlen=50)
        self._event_queue = deque(maxlen=100)
        self._writer_thread = None

    def log(self, event_type: str, ntc_channel: Optional[int],
            description: str, detail: dict = None):
        """记录事件到队列"""
        self._event_queue.append({
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            'event_type': event_type,
            'ntc_channel': ntc_channel,
            'description': description,
            'detail': json.dumps(detail or {}, ensure_ascii=False),
        })

    def write_state_snapshot(self, state: dict):
        """写入运行状态快照"""
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        for ntc_ch, data in state.items():
            self._state_queue.append({
                'timestamp': ts,
                'ntc_channel': ntc_ch,
                'target_temp': data['target_temp'],
                'target_voltage': data['target_voltage'],
                'actual_voltage': data['actual_voltage'],
                'deviation': data['deviation'],
                'compensation': data['compensation'],
                'slope_current': data['slope'],
                'is_limited': 1 if data['is_limited'] else 0,
                'event_type': data['status'],
            })

    def flush(self):
        """批量写入数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 批量写事件
        while self._event_queue:
            evt = self._event_queue.popleft()
            cursor.execute(
                "INSERT INTO link_events (timestamp, event_type, ntc_channel, "
                "description, detail) VALUES (?,?,?,?,?)",
                (evt['timestamp'], evt['event_type'], evt['ntc_channel'],
                 evt['description'], evt['detail'])
            )

        # 批量写状态
        while self._state_queue:
            st = self._state_queue.popleft()
            cursor.execute(
                "INSERT INTO link_run_state (timestamp, ntc_channel, "
                "target_temp, target_voltage, actual_voltage, deviation, "
                "compensation, slope_current, is_limited, event_type) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (st['timestamp'], st['ntc_channel'], st['target_temp'],
                 st['target_voltage'], st['actual_voltage'], st['deviation'],
                 st['compensation'], st['slope_current'], st['is_limited'],
                 st['event_type'])
            )

        conn.commit()
        conn.close()
```

**定时刷新**：在 AutoCoordinator 的 `_control_loop` 中每 50 个周期（约 5 秒）调用 `self.logger.flush()`。

---

## 4. 数据库设计

> **与需求差异说明**：requirement.md IR-01 中概述为"新增 3 张表"，但实际列出 4 张表定义（channel_mapping, link_run_state, link_events, link_rules）。本设计实现全部 4 张表。

### 4.1 数据库迁移

在 `voltage_data.db` 中新增 4 张表，不改动已有表。通过命令行参数或首次启动自动执行迁移。

**迁移文件**：`auto_coordinator/migration_v1.sql`

```sql
-- 联动引擎 v1.0 数据库迁移
-- 执行时机：AutoCoordinatorApp 首次启动时自动检测并执行

CREATE TABLE IF NOT EXISTS channel_mapping (
    id INTEGER PRIMARY KEY,
    ntc_channel INTEGER NOT NULL UNIQUE,
    voltage_channel INTEGER NOT NULL,
    tolerance REAL DEFAULT 0.05,
    compensation_enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS link_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_channel INTEGER NOT NULL,
    linked_channel INTEGER NOT NULL,
    slope_multiplier REAL DEFAULT 0.5,
    UNIQUE(trigger_channel, linked_channel)
);

CREATE TABLE IF NOT EXISTS link_run_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    ntc_channel INTEGER NOT NULL,
    target_temp REAL,
    target_voltage REAL,
    actual_voltage REAL,
    deviation REAL,
    compensation REAL,
    slope_current REAL,
    is_limited INTEGER DEFAULT 0,
    event_type TEXT
);

CREATE TABLE IF NOT EXISTS link_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    event_type TEXT NOT NULL,
    ntc_channel INTEGER,
    description TEXT,
    detail TEXT
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_link_state_ts ON link_run_state(timestamp);
CREATE INDEX IF NOT EXISTS idx_link_events_ts ON link_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_link_events_type ON link_events(event_type);
```

### 4.2 ER 图

```
┌──────────────────┐     ┌──────────────────┐
│ channel_mapping   │     │ link_rules        │
│──────────────────│     │──────────────────│
│ PK id            │     │ PK id            │
│ UNQ ntc_channel  │◄────│ FK trigger_channel│ (逻辑外键)
│     voltage_channel    │ FK linked_channel │ (逻辑外键)
│     tolerance     │     │    slope_multiplier│
│     compensation_ │     └──────────────────┘
│     enabled       │
└──────────────────┘

┌──────────────────┐     ┌──────────────────┐
│ link_run_state    │     │ link_events       │
│──────────────────│     │──────────────────│
│ PK id            │     │ PK id            │
│    timestamp     │     │    timestamp     │
│    ntc_channel   │     │    event_type    │
│    target_temp   │     │    ntc_channel   │
│    target_voltage│     │    description   │
│    actual_voltage│     │    detail (JSON) │
│    deviation     │     └──────────────────┘
│    compensation  │
│    slope_current │
│    is_limited    │
│    event_type    │
└──────────────────┘
```

### 4.3 数据清理策略

| 表 | 策略 | 实现 |
|------|------|------|
| `link_run_state` | 保留最近 7 天 | 启动时执行 `DELETE FROM link_run_state WHERE timestamp < datetime('now', '-7 days')` |
| `link_events` | 用户手动清理 | 在 UI 中提供"清除日志"按钮 |

---

## 5. NTC.py 改造设计

### 5.1 改造范围

在现有 `MainWindow` 类中增加：

| 改动项 | 位置 | 说明 |
|--------|------|------|
| `watch_timer` | `__init__()` | 新 QTimer，200ms 周期轮询 `_external_command` |
| `heartbeat_timer` | `__init__()` | 新 QTimer，500ms 周期写入 `_heartbeat` |
| `check_external_commands()` | 新方法 | 处理联动引擎下发的控制指令 |
| `update_heartbeat()` | 新方法 | 写入心跳信息到 NTC.json |
| `auto_ramp()` 联动模式开关 | 修改 `auto_ramp()` | 检测 `_external_command` 中是否有 `mode: "coordinator"`，有则跳过自身斜坡逻辑 |
| `save_cfg()` 安全写入 | 修改 `save_cfg()` | 使用临时文件+原子重命名 |

### 5.2 新增方法伪代码

```python
# 在 MainWindow.__init__() 末尾增加
self.watch_timer = QTimer(self)
self.watch_timer.timeout.connect(self.check_external_commands)
self.watch_timer.start(200)

self.heartbeat_timer = QTimer(self)
self.heartbeat_timer.timeout.connect(self.update_heartbeat)
self.heartbeat_timer.start(500)

def update_heartbeat(self):
    """向 NTC.json 写入心跳"""
    try:
        cfg = json.loads(CFG_JSON_FILE.read_text(encoding='utf-8'))
    except:
        return
    cfg['_heartbeat'] = {
        'timestamp': time.time(),
        'pid': os.getpid(),
    }
    tmp = CFG_JSON_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp.replace(CFG_JSON_FILE)

def check_external_commands(self):
    """检测联动引擎下发的控制指令"""
    try:
        cfg = json.loads(CFG_JSON_FILE.read_text(encoding='utf-8'))
    except:
        return

    external_cmd = cfg.get('_external_command', {})
    if not external_cmd:
        return

    # 处理各通道指令
    for ch_str, cmd in external_cmd.items():
        ch_idx = int(ch_str)

        if 'temp' in cmd:
            new_temp = max(-50.0, min(150.0, float(cmd['temp'])))
            self.current_t[ch_idx] = new_temp
            self._update_ui_from_data(ch_idx)
            self.update_output_immediately(ch_idx)

        if 'slope' in cmd:
            new_slope = max(-600.0, min(600.0, float(cmd['slope'])))
            # 更新 UI（阻塞信号避免递归）
            sp_slope = self.ntc_widgets[ch_idx][6]
            sp_slope.blockSignals(True)
            sp_slope.setValue(new_slope)
            sp_slope.blockSignals(False)
            self.cfg['slope'][ch_idx] = new_slope

        if 'auto_run' in cmd:
            chk_auto = self.ntc_widgets[ch_idx][7]
            chk_auto.blockSignals(True)
            chk_auto.setChecked(bool(cmd['auto_run']))
            chk_auto.blockSignals(False)
            self.cfg['auto_run'][ch_idx] = bool(cmd['auto_run'])

    # 清除已处理的指令
    cfg['_external_command'] = {}
    tmp = CFG_JSON_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp.replace(CFG_JSON_FILE)

def auto_ramp(self):
    """自动升温/降温循环（改造：支持联动模式）"""
    # 检测联动模式 — _mode 是 NTC.json 顶层字段
    try:
        cfg = json.loads(CFG_JSON_FILE.read_text(encoding='utf-8'))
    except:
        cfg = {}
    if cfg.get('_mode') == 'coordinator':
        # 联动引擎接管，跳过自身斜坡逻辑
        return

    # 原有 auto_ramp 逻辑不变
    # ... (保留原代码)
```

### 5.3 save_cfg() 安全写入改造

```python
def save_cfg(self):
    """保存配置（改造：原子写入）"""
    # ... 收集 cfg 逻辑不变 ...

    # 替换原有直接写入
    tmp_path = CFG_JSON_FILE.with_suffix('.tmp')
    try:
        tmp_path.write_text(
            json.dumps(self.cfg, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        tmp_path.replace(CFG_JSON_FILE)
    except:
        pass
```

---

## 6. AutoCoordinatorApp（联动引擎 UI）

### 6.1 主窗口布局

**文件**：`auto_coordinator/app.py`

```
┌──────────────────────────────────────────────────────────────┐
│ [通道绑定] [联动规则] [状态看板] [事件日志]    [启动] [暂停] [停止]  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  当前激活 Tab 的内容区域                                       │
│                                                              │
│  Tab 1: 通道绑定（表格编辑 6 行 NTC CH ↔ 1.py CH）            │
│  Tab 2: 联动规则（表格编辑 Trigger CH → Linked CH）           │
│  Tab 3: 联动状态看板（实时 6 通道状态）                        │
│  Tab 4: 事件日志（列表 + 筛选 + 导出）                         │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ 状态栏: NTC.py: ●在线  |  1.py: ●在线  |  引擎: ▶运行中       │
└──────────────────────────────────────────────────────────────┘
```

### 6.2 窗口类结构

```python
class AutoCoordinatorApp(QMainWindow):
    """联动引擎主窗口"""

    state_updated = pyqtSignal(dict)  # 线程安全信号

    def __init__(self):
        super().__init__()
        self.setWindowTitle("工装测试联动引擎 v1.0")
        self.setMinimumSize(900, 600)

        self.engine = AutoCoordinator()
        # 通过 pyqtSignal 从引擎线程安全传递到主线程
        self.state_updated.connect(self._on_state_snapshot)
        self.engine.on_state_changed = self.state_updated.emit

        self._init_ui()
        self._init_timers()
        self._connect_buttons()

    def _connect_buttons(self):
        self.start_btn.clicked.connect(self._on_start)
        self.pause_btn.clicked.connect(self._on_pause)
        self.stop_btn.clicked.connect(self._on_stop)

    def _on_start(self):
        alive = self.engine.data_reader.check_alive(10.0)
        if not alive['ntc']:
            QMessageBox.warning(self, "无法启动", "NTC.py 未运行，请先启动 NTC.py")
            return
        if not alive['voltage']:
            QMessageBox.warning(self, "无法启动", "1.py 未运行或未采集数据")
            return
        self.engine.start()
        self.status_engine.setText("引擎: ▶运行中")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.pause_btn.setEnabled(True)

    def _on_pause(self):
        if self.engine.paused:
            self.engine.resume()
            self.pause_btn.setText("暂停")
            self.status_engine.setText("引擎: ▶运行中")
        else:
            self.engine.pause()
            self.pause_btn.setText("恢复")
            self.status_engine.setText("引擎: ⏸暂停")

    def _on_stop(self):
        self.engine.stop()
        self.status_engine.setText("引擎: ⏹停止")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("暂停")

    def _on_state_snapshot(self, state_snapshot: dict):
        """在主线程安全更新 UI"""
        self.dashboard_tab.update_from_snapshot(state_snapshot)

    def _init_ui(self):
        # 工具栏
        toolbar = QToolBar()
        self.start_btn = QPushButton("启动")
        self.pause_btn = QPushButton("暂停")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        toolbar.addWidget(self.start_btn)
        toolbar.addWidget(self.pause_btn)
        toolbar.addWidget(self.stop_btn)
        self.addToolBar(toolbar)

        # 选项卡
        self.tab_widget = QTabWidget()
        self.bind_tab = ChannelBindWidget(self.engine.data_reader)
        self.rules_tab = LinkRulesWidget()
        self.dashboard_tab = StatusDashboard()
        self.log_tab = LogPanel()
        self.tab_widget.addTab(self.bind_tab, "通道绑定")
        self.tab_widget.addTab(self.rules_tab, "联动规则")
        self.tab_widget.addTab(self.dashboard_tab, "状态看板")
        self.tab_widget.addTab(self.log_tab, "事件日志")
        self.setCentralWidget(self.tab_widget)

        # 状态栏
        self.status_ntc = QLabel("NTC.py: --")
        self.status_v = QLabel("1.py: --")
        self.status_engine = QLabel("引擎: ⏹停止")
        self.statusBar().addWidget(self.status_ntc)
        self.statusBar().addWidget(self.status_v)
        self.statusBar().addWidget(self.status_engine)

    def _init_timers(self):
        # 存活检测
        self.alive_timer = QTimer()
        self.alive_timer.timeout.connect(self._check_alive)
        self.alive_timer.start(2000)  # 每2秒

    def _check_alive(self):
        """更新存活状态显示"""
        alive = self.engine.data_reader.check_alive(10.0)
        self.status_ntc.setText(
            f"NTC.py: {'●在线' if alive['ntc'] else '○离线'}")
        self.status_v.setText(
            f"1.py: {'●在线' if alive['voltage'] else '○离线'}")
```

### 6.3 通道绑定页设计

```
┌─ 通道绑定配置 ──────────────────────────────────────────┐
│                                                         │
│  ┌──────────┬──────────────┬──────────┬──────────────┐  │
│  │ NTC通道  │ 绑定1.py通道  │ 容差(V)  │ 自动补偿      │  │
│  ├──────────┼──────────────┼──────────┼──────────────┤  │
│  │ CH1      │ [CH11  ▼]    │ [0.050]  │ [✔]          │  │
│  │ CH2      │ [CH12  ▼]    │ [0.050]  │ [✔]          │  │
│  │ CH3      │ [CH13  ▼]    │ [0.080]  │ [✔]          │  │
│  │ CH4      │ [CH14  ▼]    │ [0.050]  │ [ ]           │  │
│  │ CH5      │ [CH15  ▼]    │ [0.050]  │ [✔]          │  │
│  │ CH6      │ [CH16  ▼]    │ [0.050]  │ [✔]          │  │
│  └──────────┴──────────────┴──────────┴──────────────┘  │
│                                                         │
│  [保存配置] [导出模板] [导入模板]                          │
└─────────────────────────────────────────────────────────┘
```

**控件说明**：

| 列 | 控件类型 | 数据源/约束 |
|------|----------|------------|
| NTC 通道 | 只读 Label | CH1~CH6 固定 |
| 绑定 1.py 通道 | QComboBox(1-18) | 从 `channel_mapping.voltage_channel` 读，去重约束 |
| 容差(V) | QDoubleSpinBox(0.01~1.0, 步进0.01) | 从 `channel_mapping.tolerance` 读 |
| 自动补偿 | QCheckBox | 从 `channel_mapping.compensation_enabled` 读 |

保存时批量更新 `channel_mapping` 表，并通知 `DataReader` 重新 `load_mapping()`。

### 6.4 联动规则页设计

```
┌─ 联动规则配置 ──────────────────────────────────────────┐
│                                                         │
│  ┌──────────────┬──────────────┬──────────────────┐     │
│  │ 触发通道      │ 联动通道      │ 降斜率系数        │     │
│  ├──────────────┼──────────────┼──────────────────┤     │
│  │ [CH1  ▼]     │ [CH2  ▼]     │ [0.40]           │     │
│  │ [CH1  ▼]     │ [CH3  ▼]     │ [0.30]           │     │
│  │ [+ 添加规则]  │              │                  │     │
│  └──────────────┴──────────────┴──────────────────┘     │
│                                                         │
│  [保存]                                                  │
└─────────────────────────────────────────────────────────┘
```

保存时写入 `link_rules` 表。

### 6.5 状态看板设计

```
┌─ 联动状态看板 ─────────────────────────────────────────────────────────┐
│                                                                        │
│  ┌──────┬───────┬───────┬───────┬───────┬──────┬──────┬──────┬──────┐ │
│  │ 通道 │ 设温℃ │ 期望V │ 实际V │ 偏差V │ 补偿 │ 斜率  │ 限幅 │ 自动 │ │
│  ├──────┼───────┼───────┼───────┼───────┼──────┼──────┼──────┼──────┤ │
│  │ CH1  │ 25.0  │ 2.500 │ 2.520 │ 0.020 │ 0.00 │ 5.0  │  --  │ ✔   │ │
│  │ CH2  │ 30.0  │ 2.100 │ 2.650 │ 0.550 │ 0.00 │ 2.5  │ 限幅 │ ✔   │ │ ← 红
│  │ CH3  │ 15.0  │ 3.200 │ 3.210 │ 0.010 │ 0.00 │ -3.0 │  --  │ ✔   │ │
│  │ ...  │ ...   │ ...   │ ...   │ ...   │ ...  │ ...  │ ...  │ ...  │ │
│  └──────┴───────┴───────┴───────┴───────┴──────┴──────┴──────┴──────┘ │
│                                                                        │
│  颜色规则: 白色=正常  黄色=偏差>容差  橙色=偏差>0.3V  红色=偏差>0.5V    │
└────────────────────────────────────────────────────────────────────────┘
```

刷新机制：`on_engine_state_changed` 回调触发时，`blockSignals` 方式更新所有单元格。

### 6.6 事件日志页设计

```
┌─ 联动事件日志 ──────────────────────────────────────────┐
│                                                         │
│  筛选: [事件类型 ▼] [通道 ▼] [开始] [结束] [查询] [导出] │
│                                                         │
│  ┌──────┬──────────┬──────┬──────────────────────┐      │
│  │ 时间  │ 事件类型  │ 通道 │ 描述                  │      │
│  ├──────┼──────────┼──────┼──────────────────────┤      │
│  │13:01 │CMPENSATE │ CH1  │ 补偿 -0.050V          │      │
│  │13:02 │SLOPE_ADJ │ CH2  │ 超阈值→斜率5.0→2.5    │      │
│  │13:02 │LINK_TRIG │ CH3  │ 联动降斜率3.0→2.1     │      │
│  │ ...  │...       │ ...  │ ...                   │      │
│  └──────┴──────────┴──────┴──────────────────────┘      │
│                                                         │
│  [清除日志]                                              │
└─────────────────────────────────────────────────────────┘
```

数据源：`link_events` 表，定时刷新（500ms），支持 SQL WHERE 筛选。

**event_type 枚举扩展**（基于 requirement.md FR-09.2，新增 `SLOPE_ADJ_ESCALATE`）：

| event_type | 含义 | 来源模块 |
|------------|------|----------|
| `DEV_ALARM` | 偏差告警 | DeviationChecker |
| `LIMIT_HIT` | 到达限温限幅 | RampDriver |
| `COMPENSATION` | 自动补偿执行 | Compensator |
| `SLOPE_ADJ` | 超阈值降斜率 | SlopeGuard |
| `SLOPE_ADJ_ESCALATE` | 降斜率后仍上升 → 再次降斜(FR-04.4) | SlopeGuard.check_re_reduce |
| `LINK_TRIGGER` | 通道联动触发 | LinkTrigger |
| `HW_FAULT` | 硬件故障级大偏差 | DeviationChecker (>0.5V) |
| `RAMP_COMPLETE` | 斜坡完成 | RampDriver |
| `AUTO_STOP` | 自动停止 | AutoCoordinator (上位机离线/大偏差无改善) |

---

## 7. 启动/停止与状态机

### 7.1 启动流程

```
用户点击"启动"
    │
    ▼
检查 NTC.py 存活
    ├── 离线 → 弹出提示"NTC.py 未运行，请先启动 NTC.py" → 终止
    └── 在线 → 继续
检查 1.py 存活
    ├── 离线 → 弹出提示"1.py 未运行或未采集数据" → 终止
    └── 在线 → 继续
检查通道绑定完整性
    ├── 存在无绑定的通道 → 警告提示但允许继续（无绑定通道不参与联动）
    └── 完整 → 继续
下发联动模式指令到 NTC.py
    │  NTC.json._mode = "coordinator" (顶层字段)
    │  （NTC.py 检测到此标志后停用自身 auto_ramp 斜坡逻辑）
    │
启动 AutoCoordinator 控制线程
    │
状态栏: "引擎: ▶运行中"
启动成功
```

### 7.2 状态机

```
         ┌──────────┐
         │  IDLE    │  ← 初始状态 / 用户点击停止后
         │ (未启动)  │
         └────┬─────┘
              │ 用户点击"启动" → 通过自检
              ▼
         ┌──────────┐
         │ RUNNING  │  ← 正常闭环运行
         │ (运行中)  │  │
         └────┬─────┘  │ 异常检测:
              │         ├─ NTC.py 离线
              │         ├─ 1.py 离线
              │         └─ 大偏差无改善(>10次补偿失败)
              ▼
         ┌──────────┐
         │ PAUSED   │  ← 用户点击"暂停"
         │ (暂停)    │──── 用户点击"恢复" → RUNNING
         └────┬─────┘
              │ 用户点击"停止"
              ▼
         ┌──────────┐
         │ STOPPED  │  ← 自动停止: 异常/全通道完成
         │ (已停止)  │──── 用户点击"启动" → 自检 → RUNNING
         └──────────┘
```

### 7.3 状态转换表

| 当前状态 | 事件 | 下一状态 | 动作 |
|----------|------|----------|------|
| IDLE | 用户点击启动 + 自检通过 | RUNNING | 下发动模式, 启动控制线程 |
| IDLE | 用户点击启动 + 自检失败 | IDLE | 弹出错误提示 |
| RUNNING | 用户点击暂停 | PAUSED | 线程暂停循环 |
| PAUSED | 用户点击恢复 | RUNNING | 线程恢复循环 |
| RUNNING/PAUSED | 用户点击停止 | IDLE | 停止线程, 复位 NTC.py |
| RUNNING | NTC.py 离线 | STOPPED | 停止线程, 告警日志 |
| RUNNING | 1.py 离线 | STOPPED | 停止线程, 告警日志 |
| RUNNING | 全通道到达限温 | STOPPED | 停止线程, "测试完成"通知 |
| STOPPED | 用户点击启动 + 自检通过 | RUNNING | 重新启动 |

---

## 8. 配置文件设计

### 8.1 联动引擎配置 (auto_coordinator_config.json)

从 `auto_coordinator/` 目录加载：

```json
{
  "control_interval_ms": 100,
  "max_deviation_hw_v": 0.5,
  "compensation_damping": 0.5,
  "compensation_max_per_cycle_v": 0.2,
  "compensation_retry_max": 10,
  "slope_reduce_default": 0.5,
  "slope_restore_cycles": 5,
  "alive_timeout_s": 10.0,
  "min_slope": 0.1,
  "state_retention_days": 7,
  "log_level": "INFO"
}
```

**设计原则** (NFR-04.2)：所有 `AutoCoordinator` 类中 `CAPITALIZED_CONSTANTS` 将在 `__init__()` 中从此配置加载，避免硬编码。

### 8.2 联动测试场景模板 (scenes/*.json)

```
auto_coordinator/
├── scenes/
│   ├── 冰箱全温验证.json
│   ├── 空调快速测试.json
│   └── 洗碗机标准测试.json
```

模板格式：

```json
{
  "name": "冰箱全温验证",
  "description": "标准冰箱主控板全温度范围验证",
  "channel_mapping": [
    {"ntc_channel": 1, "voltage_channel": 11, "tolerance": 0.05, "compensation": true},
    {"ntc_channel": 2, "voltage_channel": 12, "tolerance": 0.05, "compensation": true},
    {"ntc_channel": 3, "voltage_channel": 13, "tolerance": 0.08, "compensation": true}
  ],
  "link_rules": [
    {"trigger": 1, "linked": 2, "multiplier": 0.4}
  ],
  "ntc_params": {
    "ntc_temps": [25.0, -18.0, 25.0, 25.0, 25.0, 25.0],
    "slope": [1.0, -2.0, 3.0, 0.0, 0.0, 0.0],
    "limit_temp_high": [5, 150, 40, 150, 150, 150],
    "limit_temp_low": [-50, -18, -50, -50, -50, -50],
    "auto_run": [true, true, true, false, false, false]
  }
}
```

---

## 9. 接口设计汇总

### 9.1 跨进程接口

| 接口 | 方向 | 通道 | 格式 | 频率 |
|------|------|------|------|------|
| voltage_data 读 | 1.py → 引擎 | `voltage_data.db` / `voltage_data` 表最后一行 | SQL | 每周期(100ms) |
| NTC 状态读 | NTC.py → 引擎 | `NTC.json` | JSON | 每周期(100ms) |
| 控制指令写 | 引擎 → NTC.py | `NTC.json` / `_external_command` | JSON | 按需 |
| 心跳读 | NTC.py → 引擎 | `NTC.json` / `_heartbeat` | JSON | 每 500ms 写 / 每 100ms 读 |
| 阈值读 | 1.py 配置 → 引擎 | `config.json` / `thresholds` | JSON | 按需(缓存) |

### 9.2 内部接口

| 接口 | 说明 |
|------|------|
| `AutoCoordinator.on_state_changed(state: dict)` | 引擎 → UI 回调，控制线程完成后触发 |
| `DataReader.read_voltage_data() → list[float]` | 返回 18 路最新电压 |
| `DataReader.read_ntc_state() → dict` | 返回 NTC 当前完整状态 |
| `DataReader.calc_expected_voltage(temp, curve, pullup) → float` | 引擎内计算期望电压 |
| `NTCConverter.voltage2temp_approx(v) → float` | 新增加逆查表，电压 → 近似温度 |

---

## 10. 安全设计

### 10.1 多层安全钳位

```
用户设定/引擎计算
       │
       ▼
  [温度钳位] -50 ≤ T ≤ 150  ℃          ← C-07 约束
       │
       ▼
  [电压钳位] 0 ≤ V ≤ 5.0   V          ← NFR-05.1
       │
       ▼
  [写入钳位] HKModule.write_voltage
  val = max(0, min(5000, int(v*1000)))  ← 硬件层最后防线
```

### 10.2 斜率安全

- 降斜率下限 0.1 °C/min（不允许降至0除非用户手动设0）
- 正斜率通道在到达上限后自动停在限制值（不会溢出）
- 负斜率通道在到达下限后自动停在限制值

### 10.3 补偿安全

- 单次补偿上限 0.2V（防止补偿过度）
- 阻尼系数 0.5（50% 补偿，防止过冲振荡）
- 连续失败 10 次后放弃补偿并告警

---

## 11. 目录结构

```
jiaohu/
├── 1.py                          # 现有（少量改造）
├── NTC.py                        # 现有（增加 watch/heartbeat timer）
├── setup.py                      # 现有（不变）
├── config.json                   # 现有（不变，联动引擎只读）
├── NTC.json                      # 现有（增加 _heartbeat, _external_command）
├── NTC.xlsx                      # 现有（不变）
├── voltage_data.db               # 现有（增加 4 张新表）
│
├── requirement.md                # 需求文档
├── design.md                     # 本文档
├── 自动化联动方案.md               # 联动方案
├── README.md                     # 项目说明
│
├── auto_coordinator/             # 联动引擎模块（新增）
│   ├── __init__.py
│   ├── app.py                    # AutoCoordinatorApp 主窗口
│   ├── engine.py                 # AutoCoordinator 核心引擎
│   ├── data_reader.py            # DataReader + NTCConverter (含逆查表)
│   ├── deviation_checker.py      # DeviationChecker
│   ├── compensator.py            # Compensator
│   ├── slope_guard.py            # SlopeGuard
│   ├── link_trigger.py           # LinkTrigger
│   ├── ramp_driver.py            # RampDriver
│   ├── link_logger.py            # LinkLogger
│   ├── ntc_json_writer.py        # NTCJsonWriter (共享原子写入器)
│   ├── widgets/                  # UI 组件
│   │   ├── __init__.py
│   │   ├── bind_tab.py           # 通道绑定页
│   │   ├── rules_tab.py          # 联动规则页
│   │   ├── dashboard_tab.py      # 状态看板页
│   │   └── log_tab.py            # 事件日志页
│   ├── scenes/                   # 预置测试场景模板
│   │   ├── 冰箱全温验证.json
│   │   ├── 空调快速测试.json
│   │   └── 洗碗机标准测试.json
│   ├── config.json               # 联动引擎自身配置
│   └── migration_v1.sql          # 数据库迁移脚本
│
├── tests/                        # 测试（新增）
│   ├── test_deviation_checker.py
│   ├── test_compensator.py
│   ├── test_slope_guard.py
│   ├── test_data_reader.py
│   └── test_engine.py
│
└── run_coordinator.bat           # 一键启动脚本（新增）
```

---

## 12. 需求到模块的追溯矩阵

| 需求编号 | 对应模块 | 对应文件 |
|----------|----------|----------|
| FR-01 通道绑定 | ChannelBindWidget + DataReader.load_mapping() | `widgets/bind_tab.py`, `data_reader.py` |
| FR-02 偏差检测 | DeviationChecker + DataReader | `deviation_checker.py`, `data_reader.py` |
| FR-03 自动补偿 | Compensator + NTCConverter.voltage2temp_approx() | `compensator.py`, `data_reader.py` |
| FR-04 电压超标降斜率 | SlopeGuard | `slope_guard.py` |
| FR-05 通道联动 | LinkTrigger + link_rules 表 | `link_trigger.py` |
| FR-06 自动斜坡推进 | RampDriver | `ramp_driver.py` |
| FR-07 配置管理 | 导出/导入 JSON 模板 | `widgets/bind_tab.py`, `scenes/` |
| FR-08 状态看板 | StatusDashboard | `widgets/dashboard_tab.py` |
| FR-09 事件日志 | LogPanel + LinkLogger | `widgets/log_tab.py`, `link_logger.py` |
| FR-10 启动/停止 | AutoCoordinatorApp + 状态机 | `app.py`, `engine.py` |
| FR-11 存活检测 | DataReader.check_alive() | `data_reader.py` |
| IR-01 数据库新表 | migration_v1.sql | `migration_v1.sql` |
| IR-02 NTC.json 扩展 | NTC.py 改造 (heartbeat + external_command) | `NTC.py` |
| IR-03 控制指令通道 | NTCJsonWriter.write_cmd (共享写入器) | `ntc_json_writer.py`, `compensator.py`, `ramp_driver.py`, `slope_guard.py` |
| IR-04 config.json 读 | DataReader.read_thresholds() | `data_reader.py` |
| NFR-05 安全钳位 | 各模块内部钳位逻辑 | `compensator.py`, `ramp_driver.py`, `slope_guard.py`, `ntc_json_writer.py` |
| — | NTCJsonWriter (共享原子写入器，消除竞态) | `ntc_json_writer.py` |

---

## 13. 关键决策记录

| 决策 | 选项 | 选定方案 | 理由 |
|------|------|----------|------|
| 部署形式 | 独立进程 / 嵌入线程 / 嵌入新 GUI | **嵌入新 GUI 线程** | 需求 C-02 允许后续决定；GUI 线程模式便于状态看板高频刷新，减少 IPC 开销 |
| NTC 曲线加载 | 复用 NTC.py 中的 NTCConverter / 独立复制实现 | **独立复制 + 扩展逆查表** | 避免跨文件循环导入；需要新增 `voltage2temp_approx` 逆向查表以支持补偿 |
| JSON 原子写入 | 直接写 / 临时文件+os.rename | **临时文件+Path.replace** | Windows 上 `os.replace` 是原子操作，断电不丢数据（NFR-02.4） |
| DB 写入频率 | 每周期 / 批量定时 | **批量定时（每5s）** | 每周期写入 6 行 × 100ms = 60 次/s，高 IO 压力；批量写入降低 DB 锁竞争 |
| NTC.py 斜坡权属 | 完全移交 / 双模式共存 | **双模式共存** | NFR-03.4 要求引擎未启动时 NTC.py 正常工作；通过 `_mode` 标志切换 |
| NTC.json 写入架构 | 各模块各自读写 / 共享写入器 | **共享 NTCJsonWriter** | 多模块并发读-改-写会产生覆盖；统一写入器 + 内部锁消除竞态条件 |
| 多级降斜率 (FR-04.4) | 单次降斜 / 多次级联降斜 | **多次级联降斜 (max 2级)** | 降斜率后若回读仍持续上升 3 周期 → 再次降斜并触发 `SLOPE_ADJ_ESCALATE` 告警 |
| 线程间 UI 更新 | 直接回调 / pyqtSignal | **pyqtSignal** | 引擎线程直接操作 PyQt5 控件会崩溃；通过 signal/slot 机制安全投递到主线程 |
| 输出复位 (FR-10.5) | 仅停止线程 / 复位到初始状态 | **复位到 25°C/斜率0/auto_run关** | 停止时通过 `write_reset_all_outputs` 将所有通道写回安全初始状态 |

<br>

_文档结束_
