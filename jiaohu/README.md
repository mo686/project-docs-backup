# 家电工装测试上位机系统

基于 PyQt5 + Modbus RTU 协议的家电工装数据采集与模拟输出上位机。

## 应用领域

适用于家电产品（空调、冰箱、洗衣机等）生产线的工装测试环节：

- **电压采集 (1.py)**：在线监测工装治具中多达18路电压信号，用于验证 PCB 板各测试点的电压是否在规格范围内
- **NTC 温度模拟 (NTC.py)**：通过6通道独立模拟 NTC 热敏电阻的温度-电压曲线，向被测主控板输出等效电压，用于模拟不同温度环境下的传感器信号，验证主控板在各种温度工况下的响应逻辑

## 项目文件

| 文件 | 说明 |
|------|------|
| `1.py` | 18路电压采集上位机，支持实时波形、事件告警、数据库存储 |
| `NTC.py` | 6通道 NTC 温度模拟上位机，支持滑块/斜坡/自动模式控制电压输出 |
| `setup.py` | 一键安装所有依赖 |
| `config.json` | 1.py 配置文件（阈值、通道名、显示模式） |
| `NTC.json` | NTC.py 配置文件（串口、曲线、限温、斜率等） |
| `NTC.xlsx` | NTC 温度-电阻曲线数据表 |
| `voltage_data.db` | SQLite 数据库，存储采集数据和事件记录 |

## 快速开始

### 1. 安装依赖

```powershell
python setup.py
```

需要安装的库：

| 库 | 用途 |
|---|---|
| PyQt5 | GUI 界面框架 |
| pyqtgraph | 实时波形绘制（高刷新率，低 CPU 占用） |
| pyserial | 串口端口扫描 |
| numpy | 数值计算 |
| pymodbus | Modbus RTU 客户端（1.py 使用） |
| minimalmodbus | Modbus RTU 精简客户端（NTC.py 使用） |
| pandas | 数据处理与 Excel 导入导出 |
| openpyxl | Excel 文件写入引擎 |

### 2. 运行 18 路电压采集上位机

```powershell
python 1.py
```

### 3. 运行 6 通道 NTC 温度模拟上位机

```powershell
python NTC.py
```

---

## 1.py — 18路电压采集上位机

### 应用场景

在生产线工装测试中，被测家电 PCB 板被放置在测试治具上，治具通过探针将板上 18 个关键测试点引出。每个测试点的电压反映了对应电路模块的工作状态（如电源轨、信号放大输出、传感器接口等）。上位机通过 Modbus RTU 从硬件采集模块轮询读取这 18 路电压，并实时判定是否在合格阈值范围内。

### 核心功能

| 功能模块 | 说明 |
|---|---|
| **串口连接** | 自动扫描系统可用串口，支持 4800~115200 多种波特率，连接后自动执行模块自检 |
| **数据采集** | 独立采集线程以可配置的间隔（10~1000ms）轮询 Modbus 输入寄存器，支持5次重试与自动重连 |
| **实时数据显示** | 4行9列表格同时展示18通道名称与实时电压值，超阈值通道自动红色高亮 |
| **波形图绘制** | pyqtgraph 实时波形，支持18通道独立选择，带图例、缩放、拖动；两种显示模式（实时更新/拖动分析） |
| **事件告警** | 当任意通道电压超过设定阈值时自动记录事件，记录告警通道名、开始时间、持续时长、备注 |
| **数据库存储** | 数据自动存入 SQLite（WAL模式、批量写入），事件记录独立存储，支持后续追溯 |
| **数据导出** | 支持将采集数据和事件记录分别导出为 Excel 文件 |

### 架构工作流

```
┌─────────────────────────────────────────────────────────────────┐
│                     VoltageMonitoringApp (主窗口)                │
│                                                                 │
│  init_database() → load_config() → init_ui() → init_plot()     │
│                                          ↓                      │
│                              init_connections()                 │
│                                          ↓                      │
│              用户点击「连接」→ toggle_connection()               │
│              用户点击「开始采样」→ start_acquisition()           │
└─────────────────────────────────────────────────────────────────┘
```

### 线程模型

```
┌──────────────────┐     pyqtSignal      ┌───────────────────────┐
│ DataAcquisition  │ ──── data_updated → │ VoltageMonitoringApp  │
│    Thread        │     (timestamp,     │     (主线程 UI)        │
│                  │      data[18])      │                       │
│  ┌────────────┐  │                     │  handle_data_update() │
│  │ QTimer     │  │                     │       ↓               │
│  │ (定时触发)  │  │                     │  data_buffer.append() │
│  │     ↓      │  │                     │       ↓               │
│  │ collect_   │  │                     │  UI定时器 (10ms)      │
│  │ data()     │  │                     │       ↓               │
│  │     ↓      │  │                     │  update_ui_from_      │
│  │ read_      │  │                     │  buffer()             │
│  │ voltage_   │  │                     │   ├─ check_events()   │
│  │ data()     │  │                     │   ├─ update_data_     │
│  └────────────┘  │                     │   │   table()         │
└──────────────────┘                     │   └─ update_plot()    │
                                         └───────────────────────┘
┌──────────────────┐
│ DataStorage      │     从 data_buffer 消费数据, 批量写入 DB
│    Thread        │     每 100 条批量 INSERT 一次
└──────────────────┘
```

### 数据流详解

```
硬件模块 (Modbus RTU 从站)
    │
    │  RS-485 / RS-232 串口
    ▼
ModbusRTUClient.read_voltage_data()
    │  read_input_registers(0x0000, 18)
    │  5次重试 + 自动重连机制
    ▼
原始寄存器值 [reg0, reg1, ..., reg17]
    │
    │  _parse_value(): 可变小数点格式解码
    │  高2位=小数位数, 低14位=数值
    ▼
电压数据 [v0, v1, ..., v17] (float, 3位精度)
    │
    ├──→ DataAcquisitionThread ── pyqtSignal ──→ 主线程 UI
    │       │                                       │
    │       └──→ data_buffer (deque, maxlen=1000)   ├── check_events()
    │                  │                            │   ┌ 超阈值 → 创建告警事件
    │                  │                            │   └ 恢复正常 → "系统正常"事件
    │                  │                            │
    │                  ├──→ DataStorageThread       ├── update_data_table()
    │                  │    批量写入 SQLite           │   更新表格显示 + 超阈值变红
    │                  │    ┌ voltage_data 表         │
    │                  │    └ events 表              └── update_plot()
    │                  │                                pyqtgraph 实时波形更新
    │                  │                                18条曲线 + 阈值虚线
    │                  │
    │                  └──→ 导出功能
    │                        pandas → Excel (.xlsx)
    │
    ▼
voltage_data.db (SQLite, WAL 模式)
    ├── voltage_data: id, timestamp, channel1~channel18
    └── events: id, timestamp, description, duration, note
```

### 事件状态机

```
         ┌──────────┐
         │ 系统正常   │ ← 初始状态 / 所有通道低于阈值
         │ (is_quiet │
         │  = True) │
         └─────┬────┘
               │ 任意通道电压 > 阈值
               ▼
         ┌──────────┐
         │ 告警状态   │
         │ (记录通道名│
         │  + 开始计时)│
         └─────┬────┘
               │ 所有通道恢复正常
               ▼
         ┌──────────┐
         │ 正常事件   │  ← 记录"系统正常"事件(带持续时间)
         │ (继续计时) │
         └──────────┘
```

- 超阈值通道组合发生变化时（如从 CH1 告警变为 CH1+CH3 告警），结束当前事件并开启新事件
- 每个事件记录：序号、描述、开始时间、持续时间、备注（可手动添加）
- 两种显示模式：始终显示最新事件 / 保持当前滚动位置

### 配置项 (config.json)

```json
{
  "thresholds": [5.0, 5.0, ...],        // 18路电压阈值 (V)
  "channel_names": ["+12V电源", ...],    // 18路通道自定义名称
  "event_display_mode": 0,              // 0=始终显示最新, 1=保持滚动位置
  "plot_mode": 0,                       // 0=实时更新(近60s), 1=拖动模式
  "time_format": "seconds"              // "seconds"或"hms"格式
}
```

---

## NTC.py — 6通道 NTC 温度模拟上位机

### 应用场景

家电主控板通过 NTC 热敏电阻感知温度变化。在工装测试中，无需实际加热/制冷环境，而是用上位机模拟 NTC 在不同温度下输出的等效电压，直接注入主控板的 ADC 采样引脚。这样可以快速验证主控板在 -50°C 到 150°C 全温范围内的温度检测逻辑和对应的控制动作（如压缩机启停、电加热通断等）。

### 核心功能

| 功能模块 | 说明 |
|---|---|
| **NTC 曲线管理** | 从 Excel 加载多条 NTC 温度-电阻曲线（支持多工作表），启动时自动加载上次使用的曲线文件 |
| **温度-电压转换** | O(1) 查表转换：初始化时预计算 -50°C~150°C 全范围电压缓存，运行时直接索引查表 |
| **多通道独立** | 6个通道完全独立，各自拥有曲线选择、上拉电阻配置、高低限温、斜率设定 |
| **手动模式** | 滑块拖动（±0.1°C精度）或文本框直接输入，变更即时输出到硬件 |
| **自动斜坡模式** | 按设定斜率（°C/min）自动升/降温，到达上下限时保持，并标注"(限幅)" |
| **队列输出** | 写入请求通过队列+独立工作线程处理，避免 UI 阻塞 |
| **暂停/复位** | 支持暂停自动模式、一键复位所有通道到 25°C |
| **窗口置顶** | 支持窗口置顶，方便在示波器/万用表等工具上方显示 |

### 架构工作流

```
┌──────────────────────────────────────────────────────────────┐
│                    MainWindow (主窗口)                        │
│                                                              │
│  load_cfg() → load_ntc_curves() → init_ui()                 │
│       │                            │                         │
│       │                    为每个通道创建:                    │
│       │                    ├─ cmb_curve (曲线选择)            │
│       │                    ├─ sp_temp (温度输入)              │
│       │                    ├─ slider (滑块)                   │
│       │                    ├─ lbl_display (电压显示标签)      │
│       │                    ├─ sp_limit_high/low (限温)        │
│       │                    ├─ sp_slope (斜率)                 │
│       │                    ├─ chk_auto (自动开关)             │
│       │                    └─ sp_pullup (上拉电阻)            │
│       │                                                      │
│       │  用户操作 → 信号 → 统一处理 → 输出                    │
│       │                                                      │
│       │  定时器:                                              │
│       │  ├─ out_timer (50ms) → 周期批量输出                  │
│       │  └─ ramp_timer (100ms) → 自动斜坡计算                │
└──────────────────────────────────────────────────────────────┘
```

### 数据流详解

```
NTC.xlsx (Excel 曲线配置文件)
    │
    │  pandas.read_excel() 解析多个工作表
    │  每个工作表: Temp列 + R(电阻kΩ)列
    ▼
curves = {"10k": {-50: 361.8, -49: 361.3, ..., 150: 0.5}}
    │
    │  用户选择曲线 + 上拉电阻 → 创建 NTCConverter
    ▼
NTCConverter 初始化
    │  _calc_voltage(): 电阻线性插值 → 分压公式 → 电压
    │  Vout = 5.0 * (R_ntc / (R_ntc + R_pullup))
    ▼
voltage_cache[201] = [V(-50°C), V(-49°C), ..., V(150°C)]
    │  O(1) 查表: cache[temperature + 50]
    │
    │  用户操作触发:
    │  ┌─ 滑块拖动 → on_slider_changed()
    │  ├─ 温度框输入 → on_spinbox_changed()
    │  └─ 自动模式 → auto_ramp()
    │        └ 斜率 * Δt → 新温度 → 限温裁剪
    │
    ▼
current_t[idx] = 新温度值
    │
    ├──→ NTCConverter.temp2voltage(t) → O(1) 查缓存 → 电压值(V)
    │       │
    │       ├──→ update_label(): 显示 "25.0℃ → 2.345V"
    │       │
    │       └──→ HKModule.write_voltage(ch, v)
    │               │
    │               └── 写入队列 (_write_queue)
    │                      │
    │                      └── 工作线程 (_process_queue)
    │                             │  inst.write_register(0x0050+ch, val)
    │                             │  Modbus 功能码 06, 单寄存器写入
    │                             ▼
    │                        硬件模块 (Modbus RTU 从站)
    │                             │
    │                             ▼
    │                        被测主控板 ADC 引脚
    │                        (通过探针注入模拟 NTC 电压)
    │
    └──→ save_cfg() → NTC.json (持久化当前配置)

NTC.json (运行状态配置文件)
    ├── port, addr, baud (串口连接参数)
    ├── ntc_temps[6] (各通道当前温度)
    ├── ntc_curves[6] (各通道选择的曲线名)
    ├── limit_temp_high/low[6], slope[6], auto_run[6]
    ├── ch_names[6], pullup_resistors[6]
    └── last_loaded_excel (上次加载的Excel路径)
```

### NTC 温度-电压转换原理

```
被测主控板 ADC 输入端
         │
       ┌─┴─┐
       │   │ R_pullup (上拉电阻，如 10kΩ, 可配置 0.1~1000kΩ)
       │   │ 一端接 5V 参考电压
       └─┬─┘
         ├────→ 分压信号 → 主控板 ADC 采样
       ┌─┴─┐
       │   │ R_ntc (NTC 热敏电阻，电阻随温度变化)
       │   │ 一端接 GND
       └─┬─┘
         │
        GND

V_adc = 5.0V * R_ntc / (R_ntc + R_pullup)

温度↑ → R_ntc↓ → V_adc↓  (NTC负温度系数特性)
```

### UI 信号处理策略（防闪退核心）

```python
# 问题：滑块和温度框互相更新会导致无限信号循环，可能崩溃
# 方案：所有输入统一写入 current_t[idx]，再单向更新 UI

slider.valueChanged ──→ on_slider_changed()
                            current_t[idx] = slider_value / 10.0
                            _update_ui_from_data(idx)   # blockSignals 防递归
                            update_output_immediately(idx)

sp_temp.valueChanged ──→ on_spinbox_changed()
                            current_t[idx] = spinbox_value
                            _update_ui_from_data(idx)   # blockSignals 防递归
                            update_output_immediately(idx)
```

### 配置项 (NTC.json)

```json
{
  "port": "COM3",
  "addr": 1,
  "baud": 9600,
  "ntc_temps": [25.0, 25.0, 25.0, 25.0, 25.0, 25.0],
  "ntc_curves": ["10k", "10k", "10k", "10k", "10k", "10k"],
  "limit_temp_high": [100, 100, 100, 100, 100, 100],
  "limit_temp_low": [-50, -50, -50, -50, -50, -50],
  "slope": [5.0, 5.0, 5.0, 5.0, 5.0, 5.0],
  "auto_run": [false, false, false, false, false, false],
  "ch_names": ["CH1", "CH2", "CH3", "CH4", "CH5", "CH6"],
  "pullup_resistors": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
  "last_loaded_excel": ""
}
```

---

## 典型工装测试场景组合

### 场景一：冰箱主控板功能测试

```
┌──────────────────────────────────────────────┐
│                 测试工位                       │
│                                              │
│  冰箱主控板 (被测DUT)                         │
│  ┌───────────────────────────────┐           │
│  │  +5V电源  ─────→ 1.py CH1    │           │
│  │  +12V电源 ─────→ 1.py CH2    │           │
│  │  压缩机驱动 ────→ 1.py CH3    │           │
│  │  风机驱动 ─────→ 1.py CH4    │           │
│  │  ...                          │           │
│  │                               │           │
│  │  冷藏NTC ←── NTC.py CH1      │ (模拟冷藏室温度)  │
│  │  冷冻NTC ←── NTC.py CH2      │ (模拟冷冻室温度)  │
│  │  环温NTC ←── NTC.py CH3      │ (模拟环境温度)    │
│  └───────────────────────────────┘           │
│                                              │
│  PC 上位机                                   │
│  ├─ 1.py 监测各电压点是否在规格范围内         │
│  └─ NTC.py 模拟不同温度工况下传感器信号       │
└──────────────────────────────────────────────┘
```

### 场景二：空调主控板全温度范围验证

1. 用 **1.py** 持续监测所有供电轨和关键信号点电压，确保每次测试开始前板卡上电正常
2. 用 **NTC.py** 的自动斜坡模式模拟环境温度从 -30°C 到 60°C 变化：
   - CH1: 室内环温传感器（0~50°C，斜率3°C/min）
   - CH2: 室外盘管传感器（-30~60°C，斜率6°C/min）
   - CH3: 排气温度传感器（-20~120°C，斜率10°C/min）
3. 观察主控板在不同温度下的压缩机频率调节、四通阀切换、电加热启停等动作是否符合逻辑

---

## 版本

v2.0 — 家电工装测试优化稳定版，支持长时间运行采集与模拟
