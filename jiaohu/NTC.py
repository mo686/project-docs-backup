#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
6 通道 NTC 上位机（稳定无闪退版）
修复滑块-温度框信号循环导致的崩溃问题。
"""
import json
import sys
import time
from pathlib import Path
import pandas as pd
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import QColor
import minimalmodbus as modbus
import serial.tools.list_ports
from functools import partial

# ---------- 配置文件路径 ----------
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

CFG_EXCEL_FILE = BASE_DIR / (Path(__file__).stem + '.xlsx')
CFG_JSON_FILE = BASE_DIR / (Path(__file__).stem + '.json')

# ---------- 全局常量 ----------
REG_MAX = 5000  # 定义Modbus寄存器最大值，对应5.000V


# ---------- 从Excel加载NTC曲线 ----------
def load_ntc_curves_from_excel(file_path):
    """加载NTC曲线数据，返回字典"""
    curves = {}
    if not file_path.exists():
        print(f"配置文件 {file_path} 不存在，正在创建示例文件...")
        default_data = {
            'Temp1': list(range(-50, 151)),
            'R1': [361.8 - i * 0.5 for i in range(201)]
        }
        df = pd.DataFrame(default_data)
        df.to_excel(file_path, index=False, sheet_name='10k')
        print(f"示例配置文件已创建: {file_path}")

    try:
        xls = pd.ExcelFile(file_path)
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name)
            if len(df.columns) < 2:
                print(f"工作表 '{sheet_name}' 列数不足，跳过。")
                continue
            temp_col = df.columns[0]
            res_col = df.columns[1]
            curve_dict = {}
            for _, row in df.iterrows():
                try:
                    temp_val = int(row[temp_col])
                    res_val = float(row[res_col])
                    curve_dict[temp_val] = res_val
                except (ValueError, TypeError, KeyError):
                    continue
            if curve_dict:
                curves[sheet_name] = curve_dict
        print(f"成功加载 {len(curves)} 条NTC曲线")
    except Exception as e:
        print(f"加载Excel失败: {e}")
    return curves


# ---------- NTC转换器（带电压缓存） ----------
class NTCConverter:
    """包含缓存机制的NTC转换器，温度->电压转换为O(1)查表"""

    def __init__(self, k_table, pullup_resistor_kohm=10.0):
        if not k_table:
            raise ValueError("NTC曲线数据表不能为空")
        self.table = k_table
        self.temps = sorted(self.table.keys())
        if not self.temps:
            raise ValueError("NTC曲线数据表中无有效温度点")
        self.pullup_resistor_kohm = pullup_resistor_kohm

        # 【性能核心】初始化时生成全温度范围(-50℃~150℃)的电压缓存表
        self.voltage_cache = [self._calc_voltage(t) for t in range(-50, 151)]

    def _calc_voltage(self, t):
        """内部计算函数，仅初始化时调用一次"""
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
                r_k = self.table[self.temps[-1]]  # 修复：应从table中获取电阻值，而非温度值

        r = max(0.001, r_k * 1000)
        pullup_r = self.pullup_resistor_kohm * 1000
        return max(0.0, min(5.0, 5.0 * r / (r + pullup_r)))

    def temp2voltage(self, t):
        """运行时直接查缓存，复杂度O(1)，极快"""
        t_int = int(round(t))
        idx = max(-50, min(150, t_int)) + 50
        return self.voltage_cache[idx]


# ---------- 串口模块（使用队列和独立线程，避免阻塞） ----------
class HKModule(QObject):
    writeError = pyqtSignal(int, str)  # 通道号, 错误信息

    def __init__(self, port, addr=1, baud=9600):
        super().__init__()
        self.port = port
        self.addr = addr
        self.baud = baud
        self._instrument = None
        self._write_queue = []  # 写入任务队列
        self._queue_lock = QMutex()
        self._worker_thread = None
        self._stop_flag = False

    def _get_instrument(self):
        """获取或创建Instrument对象"""
        if self._instrument is None:
            try:
                self._instrument = modbus.Instrument(self.port, self.addr)
                self._instrument.serial.baudrate = self.baud
                self._instrument.serial.timeout = 0.2
                self._instrument.close_port_after_each_call = True
            except Exception as e:
                raise ConnectionError(f"无法初始化串口仪器: {e}")
        return self._instrument

    def write_voltage(self, ch, v):
        """将写入请求加入队列，由工作线程处理"""
        val = int(round(v * 1000))
        val = max(0, min(REG_MAX, val))
        with QMutexLocker(self._queue_lock):
            # 只保留最新的写入请求，避免队列过长
            self._write_queue = [(ch, val)]
            if self._worker_thread is None or not self._worker_thread.isRunning():
                self._start_worker()

    def _start_worker(self):
        """启动工作线程处理队列"""
        self._stop_flag = False
        self._worker_thread = QThread()
        self._worker_thread.run = self._process_queue
        self._worker_thread.start()

    def _process_queue(self):
        """工作线程主函数"""
        while not self._stop_flag:
            task = None
            with QMutexLocker(self._queue_lock):
                if self._write_queue:
                    task = self._write_queue.pop(0)

            if task:
                ch, reg_val = task
                try:
                    inst = self._get_instrument()
                    inst.write_register(0x0050 + ch - 1, reg_val, functioncode=6)
                except Exception as e:
                    self.writeError.emit(ch, str(e))

            QThread.msleep(10)  # 短暂休眠，避免空转

    def close(self):
        """关闭串口连接并停止工作线程"""
        self._stop_flag = True
        if self._worker_thread and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait()
        if self._instrument:
            try:
                self._instrument.serial.close()
            except:
                pass
            self._instrument = None


# ---------- 主窗口 ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("6CH NTC (稳定无闪退版)")
        self.resize(500, 650)
        self.cfg = self.load_cfg()

        # 尝试加载上次的Excel配置
        self.curves = self.try_load_last_excel_on_startup()
        if not self.curves:
            self.curves = load_ntc_curves_from_excel(CFG_EXCEL_FILE)
            if not self.curves:
                QMessageBox.critical(self, "错误", "无法加载NTC曲线数据")
                sys.exit(1)

        self.hk = None
        # 【核心修复】使用一个统一的温度值列表，避免信号循环
        self.current_t = [float(temp) for temp in self.cfg["ntc_temps"]]
        self.last_voltages = [0.0] * 6

        # 缓存每个通道的NTC转换器对象
        self.channel_converters = [None] * 6

        self.init_ui()

        # 初始化所有通道的转换器
        for i in range(6):
            self.update_converter(i)
            # 初始化UI显示与内部数据一致
            self._update_ui_from_data(i)

        # 定时器设置
        self.out_timer = QTimer(self)
        self.out_timer.timeout.connect(self.update_outputs)
        self.out_timer.start(50)  # 50ms周期输出

        self.ramp_timer = QTimer(self)
        self.ramp_timer.timeout.connect(self.auto_ramp)
        self.ramp_timer.start(100)  # 100ms自动模式周期

    def try_load_last_excel_on_startup(self):
        """启动时尝试加载上次使用的Excel文件"""
        last_excel_path_str = self.cfg.get("last_loaded_excel", "")
        if not last_excel_path_str:
            return {}

        last_excel_path = Path(last_excel_path_str)
        if not last_excel_path.exists():
            return {}

        print(f"尝试自动加载上次的Excel文件: {last_excel_path}")
        curves = load_ntc_curves_from_excel(last_excel_path)
        if curves and hasattr(self, 'status'):
            self.status.setText(f"已加载: {last_excel_path.name}")
        return curves

    def load_cfg(self):
        """加载或创建配置文件"""
        default = {
            "port": "COM3", "addr": 1, "baud": 9600,
            "ntc_temps": [25.0] * 6,
            "ntc_curves": ["10k"] * 6,
            "limit_temp_high": [100] * 6,
            "limit_temp_low": [-50] * 6,
            "slope": [5.0] * 6,
            "auto_run": [False] * 6,
            "ch_names": [f"CH{i}" for i in range(1, 7)],
            "pullup_resistors": [10.0] * 6,
            "last_loaded_excel": ""
        }
        cfg = {}
        config_file_path = Path(CFG_JSON_FILE)

        if config_file_path.exists():
            try:
                cfg = json.loads(config_file_path.read_text(encoding='utf-8'))
            except:
                cfg = default.copy()
        else:
            cfg = default.copy()

        # 兼容旧版配置
        if "limit_temp" in cfg:
            cfg["limit_temp_high"] = cfg["limit_temp"][:]
            cfg["limit_temp_low"] = [-50] * 6
            del cfg["limit_temp"]

        # 确保所有必要键都存在
        for k, v in default.items():
            if k not in cfg:
                cfg[k] = v[:] if isinstance(v, list) else v
            elif isinstance(v, list) and (not isinstance(cfg[k], list) or len(cfg[k]) != len(v)):
                cfg[k] = v[:]

        # 保存默认配置（如果文件不存在）
        if not config_file_path.exists():
            try:
                config_file_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')
            except:
                pass

        return cfg

    def save_cfg(self):
        """保存当前配置到文件"""
        for i, (_, sp_temp, _, _, sp_limit_high, sp_limit_low, sp_slope, chk_auto, le_name, sp_pullup) in enumerate(
                self.ntc_widgets):
            self.cfg["ntc_temps"][i] = sp_temp.value()
            self.cfg["limit_temp_high"][i] = sp_limit_high.value()
            self.cfg["limit_temp_low"][i] = sp_limit_low.value()
            self.cfg["slope"][i] = sp_slope.value()
            self.cfg["auto_run"][i] = chk_auto.isChecked()
            self.cfg["ch_names"][i] = le_name.text()
            self.cfg["pullup_resistors"][i] = sp_pullup.value()

        try:
            Path(CFG_JSON_FILE).write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding='utf-8')
        except:
            pass

    def save_ch_name(self, idx, le):
        self.cfg["ch_names"][idx] = le.text()
        self.save_cfg()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        vbox = QVBoxLayout(central)
        self.setFixedWidth(415)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)

        # 顶部工具栏
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("串口:"))
        self.cmb_port = QComboBox()
        self.cmb_port.addItems([p.device for p in serial.tools.list_ports.comports()])
        self.cmb_port.setCurrentText(self.cfg["port"])
        top_layout.addWidget(self.cmb_port, 1)

        top_layout.addWidget(QLabel("站号:"))
        self.sp_addr = QSpinBox()
        self.sp_addr.setRange(1, 255)
        self.sp_addr.setValue(self.cfg["addr"])
        top_layout.addWidget(self.sp_addr)

        top_layout.addWidget(QLabel("波特率:"))
        self.cmb_baud = QComboBox()
        self.cmb_baud.addItems(["4800", "9600", "19200", "38400", "57600", "115200"])
        self.cmb_baud.setCurrentText(str(self.cfg["baud"]))
        top_layout.addWidget(self.cmb_baud)

        top_layout.addStretch(1)
        self.chk_always_on_top = QCheckBox("置顶")
        self.chk_always_on_top.toggled.connect(self.on_always_on_top_toggled)
        top_layout.addWidget(self.chk_always_on_top)

        vbox.addLayout(top_layout)

        # 按钮行
        btn_layout = QHBoxLayout()
        self.btn_open = QPushButton("打开串口")
        self.btn_open.clicked.connect(self.open_port)
        btn_layout.addWidget(self.btn_open)

        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(lambda on: self.ramp_timer.start() if not on else self.ramp_timer.stop())
        btn_layout.addWidget(self.btn_pause)

        btn_reset = QPushButton("复位")
        btn_reset.clicked.connect(self.reset_all)
        btn_layout.addWidget(btn_reset)

        btn_load = QPushButton("加载配置")
        btn_load.clicked.connect(self.load_config_file)
        btn_layout.addWidget(btn_load)

        vbox.addLayout(btn_layout)

        # 通道控件
        self.ntc_widgets = []
        curve_names = list(self.curves.keys())
        if not curve_names:
            curve_names = ["10k"]

        channel_colors = ["#E6F3FF", "#FFF0E6", "#E6FFE6", "#FFF6E6", "#F0E6FF", "#FFE6F0"]
        border_map = {
            "#E6F3FF": "#0066CC", "#FFF0E6": "#CC6600", "#E6FFE6": "#00CC00",
            "#FFF6E6": "#CC9900", "#F0E6FF": "#6600CC", "#FFE6F0": "#CC0066"
        }

        for ch in range(1, 7):
            group = QGroupBox()
            color = channel_colors[(ch - 1) % 6]
            border = border_map.get(color, "#888888")
            group.setStyleSheet(f"""
                QGroupBox {{ border: 2px solid {border}; border-radius: 5px; margin-top: 1ex; padding: 5px; }}
                QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }}
            """)
            layout = QVBoxLayout(group)

            # 第一行：通道名、曲线选择、电压显示、上拉电阻
            row1 = QHBoxLayout()
            le_name = QLineEdit(self.cfg["ch_names"][ch - 1])
            le_name.setFixedWidth(60)
            le_name.setAlignment(Qt.AlignCenter)
            le_name.setStyleSheet(f"border: 1px solid #ccc; border-radius: 3px; background-color: {color};")
            le_name.editingFinished.connect(partial(self.save_ch_name, ch - 1, le_name))
            row1.addWidget(le_name)

            cmb_curve = QComboBox()
            cmb_curve.addItems(curve_names)
            if self.cfg["ntc_curves"][ch - 1] in curve_names:
                cmb_curve.setCurrentText(self.cfg["ntc_curves"][ch - 1])
            row1.addWidget(cmb_curve)

            lbl_display = QLabel("25.0℃ → 0.000V")
            row1.addWidget(lbl_display, 1)

            row1.addWidget(QLabel("上拉:"))
            sp_pullup = QDoubleSpinBox()
            sp_pullup.setRange(0.1, 1000.0)
            sp_pullup.setDecimals(1)
            sp_pullup.setSingleStep(0.1)
            sp_pullup.setSuffix(" k")
            sp_pullup.setValue(self.cfg["pullup_resistors"][ch - 1])
            sp_pullup.setFixedWidth(60)
            row1.addWidget(sp_pullup)

            layout.addLayout(row1)

            # 第二行：温度设定与滑块
            row2 = QHBoxLayout()
            sp_temp = QDoubleSpinBox()
            sp_temp.setRange(-50.0, 150.0)
            sp_temp.setDecimals(1)
            sp_temp.setSingleStep(0.1)
            sp_temp.setSuffix(" ℃")
            sp_temp.setValue(float(self.cfg["ntc_temps"][ch - 1]))
            sp_temp.setFixedHeight(25)
            row2.addWidget(sp_temp)

            slider = QSlider(Qt.Horizontal)
            slider.setRange(-500, 1500)
            slider.setValue(int(sp_temp.value() * 10))
            slider.setTickPosition(QSlider.TicksBelow)
            slider.setTickInterval(100)
            slider.setFixedWidth(295)
            row2.addWidget(slider)

            layout.addLayout(row2)

            # 第三行：限温、斜率、自动模式
            row3 = QHBoxLayout()
            sp_limit_high = QSpinBox()
            sp_limit_high.setRange(-50, 150)
            sp_limit_high.setValue(self.cfg["limit_temp_high"][ch - 1])
            sp_limit_high.setPrefix("上限: ")
            sp_limit_high.setFixedWidth(80)
            row3.addWidget(sp_limit_high)

            sp_limit_low = QSpinBox()
            sp_limit_low.setRange(-50, 150)
            sp_limit_low.setValue(self.cfg["limit_temp_low"][ch - 1])
            sp_limit_low.setPrefix("下限: ")
            sp_limit_low.setFixedWidth(80)
            if sp_limit_low.value() > sp_limit_high.value():
                sp_limit_low.setValue(sp_limit_high.value() - 1)
            row3.addWidget(sp_limit_low)

            sp_slope = QDoubleSpinBox()
            sp_slope.setRange(-600, 600)
            sp_slope.setSingleStep(0.1)
            sp_slope.setValue(self.cfg["slope"][ch - 1])
            sp_slope.setPrefix("斜率: ")
            sp_slope.setFixedWidth(80)
            row3.addWidget(sp_slope)

            chk_auto = QCheckBox("自动")
            chk_auto.setChecked(self.cfg["auto_run"][ch - 1])
            row3.addWidget(chk_auto)
            row3.addStretch(1)

            layout.addLayout(row3)

            # 【核心修复】修改信号连接，避免直接双向绑定
            cmb_curve.currentTextChanged.connect(partial(self.on_curve_changed, ch - 1))
            sp_pullup.valueChanged.connect(partial(self.on_pullup_changed, ch - 1))

            # 滑块值改变时，更新内部数据，然后更新UI
            slider.valueChanged.connect(partial(self.on_slider_changed, ch - 1))
            # 温度框值改变时，更新内部数据，然后更新UI
            sp_temp.valueChanged.connect(partial(self.on_spinbox_changed, ch - 1))

            vbox.addWidget(group)
            self.ntc_widgets.append(
                (cmb_curve, sp_temp, slider, lbl_display, sp_limit_high, sp_limit_low, sp_slope, chk_auto, le_name,
                 sp_pullup))

        self.status = QLabel("就绪")
        self.statusBar().addWidget(self.status)
        self.adjustSize()

    def _update_ui_from_data(self, idx):
        """根据内部数据更新UI（温度框、滑块、标签）"""
        cmb, sp_temp, slider, lbl, _, _, _, _, _, _ = self.ntc_widgets[idx]
        temp = self.current_t[idx]

        # 更新温度框（阻塞信号避免触发valueChanged）
        sp_temp.blockSignals(True)
        sp_temp.setValue(temp)
        sp_temp.blockSignals(False)

        # 更新滑块（阻塞信号避免触发valueChanged）
        slider.blockSignals(True)
        slider.setValue(int(round(temp * 10)))
        slider.blockSignals(False)

        # 更新标签
        self.update_label(idx)

    def on_always_on_top_toggled(self, checked):
        flags = self.windowFlags()
        if checked:
            self.setWindowFlags(flags | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowStaysOnTopHint)
        self.show()

    def on_slider_changed(self, idx):
        """滑块值改变时的处理"""
        _, _, slider, _, _, _, _, _, _, _ = self.ntc_widgets[idx]
        # 将滑块整数值转换为浮点温度值 (除以10)
        temp_value = slider.value() / 10.0
        # 更新内部数据
        self.current_t[idx] = temp_value
        # 根据内部数据更新UI（温度框和标签）
        self._update_ui_from_data(idx)
        # 立即输出
        self.update_output_immediately(idx)

    def on_spinbox_changed(self, idx):
        """温度框值改变时的处理"""
        _, sp_temp, _, _, _, _, _, _, _, _ = self.ntc_widgets[idx]
        # 获取温度框的浮点值
        temp_value = sp_temp.value()
        # 更新内部数据
        self.current_t[idx] = temp_value
        # 根据内部数据更新UI（滑块和标签）
        self._update_ui_from_data(idx)
        # 立即输出
        self.update_output_immediately(idx)

    def on_curve_changed(self, idx):
        self.update_converter(idx)
        self.update_label(idx)

    def on_pullup_changed(self, idx, value):
        self.update_converter(idx)
        self.update_label(idx)

    def update_converter(self, idx):
        """更新指定通道的NTC转换器（当曲线或上拉电阻改变时调用）"""
        cmb, _, _, _, _, _, _, _, _, sp_pullup = self.ntc_widgets[idx]
        curve_name = cmb.currentText()
        pullup = sp_pullup.value()

        if curve_name in self.curves:
            try:
                self.channel_converters[idx] = NTCConverter(self.curves[curve_name], pullup)
            except Exception as e:
                self.status.setText(f"CH{idx + 1} 转换器错误: {e}")
                self.channel_converters[idx] = None
        else:
            self.channel_converters[idx] = None
        self.update_label(idx)

    def update_label(self, idx):
        """更新通道的电压显示标签"""
        _, sp_temp, _, lbl, _, _, _, _, _, _ = self.ntc_widgets[idx]
        temp = sp_temp.value()
        converter = self.channel_converters[idx]

        if converter:
            voltage = converter.temp2voltage(temp)
            lbl.setText(f"{temp:.1f}℃ → {voltage:.3f}V")
        else:
            lbl.setText(f"{temp:.1f}℃ → N/A")

    def load_config_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择NTC曲线配置文件", str(Path.cwd()),
                                                   "Excel Files (*.xlsx *.xls);;All Files (*)")
        if not file_path:
            return
        self.load_excel_file(Path(file_path))

    def load_excel_file(self, file_path):
        new_curves = load_ntc_curves_from_excel(file_path)
        if not new_curves:
            QMessageBox.warning(self, "加载失败", "无效的Excel文件或格式错误")
            return

        self.curves = new_curves
        curve_names = list(self.curves.keys())

        for idx, (cmb, sp_temp, slider, lbl, _, _, _, _, _, sp_pullup) in enumerate(self.ntc_widgets):
            cmb.blockSignals(True)
            cmb.clear()
            cmb.addItems(curve_names)
            # 尝试保持原选择，否则选第一个
            current = cmb.currentText()
            if current in curve_names:
                cmb.setCurrentText(current)
            else:
                cmb.setCurrentIndex(0)
            cmb.blockSignals(False)

            self.update_converter(idx)

        self.cfg["last_loaded_excel"] = str(file_path)
        self.save_cfg()
        self.status.setText(f"已加载: {file_path.name}")

    def open_port(self):
        port = self.cmb_port.currentText()
        addr = self.sp_addr.value()
        baud = int(self.cmb_baud.currentText())

        # 关闭现有连接
        if self.hk:
            self.hk.close()
            self.hk = None

        for attempt in range(3):
            try:
                self.hk = HKModule(port, addr, baud)
                self.hk.writeError.connect(self.on_write_error)
                # 测试写入一个寄存器以验证连接
                self.hk.write_voltage(1, 0.0)
                self.cfg.update({"port": port, "addr": addr, "baud": baud})
                self.save_cfg()
                self.status.setText(f"已连接 {port} @ {baud}")
                self.btn_open.setText("已连接")
                self.btn_open.setEnabled(False)
                return
            except Exception as e:
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    QMessageBox.critical(self, "连接失败",
                                         f"无法连接到 {port} (地址:{addr}, 波特率:{baud})\n错误: {e}")
                    self.hk = None
                    self.status.setText("连接失败")
                    self.btn_open.setText("打开串口")
                    self.btn_open.setEnabled(True)

    def on_write_error(self, ch, error_msg):
        """处理串口写入错误"""
        self.status.setText(f"CH{ch} 写入失败: {error_msg}")

    def auto_ramp(self):
        """自动升温/降温循环"""
        interval_sec = self.ramp_timer.interval() / 1000.0
        limit_hit = [False] * 6

        for idx in range(6):
            _, _, _, _, sp_limit_high, sp_limit_low, sp_slope, chk_auto, _, _ = self.ntc_widgets[idx]
            if not chk_auto.isChecked():
                continue

            limit_high = sp_limit_high.value()
            limit_low = sp_limit_low.value()
            slope = sp_slope.value()
            current = self.current_t[idx]

            delta = (slope / 60.0) * interval_sec
            new_temp = current + delta

            if slope >= 0:
                if new_temp >= limit_high:
                    new_temp = limit_high
                    limit_hit[idx] = True
            else:
                if new_temp <= limit_low:
                    new_temp = limit_low
                    limit_hit[idx] = True

            self.current_t[idx] = new_temp
            # 根据内部数据更新UI
            self._update_ui_from_data(idx)
            # 立即输出
            self.update_output_immediately(idx)

        # 更新状态栏
        if self.hk:
            status_parts = []
            for idx in range(6):
                converter = self.channel_converters[idx]
                if not converter:
                    status_parts.append(f"CH{idx + 1}:Err")
                    continue
                temp = self.ntc_widgets[idx][1].value()
                voltage = converter.temp2voltage(temp)
                part = f"CH{idx + 1}:{temp:.1f}℃→{voltage:.3f}V"
                if self.ntc_widgets[idx][7].isChecked() and limit_hit[idx]:
                    part += "(限幅)"
                status_parts.append(part)
            self.status.setText(" | ".join(status_parts))

    def update_outputs(self):
        """定时更新所有通道输出"""
        if not self.hk:
            return

        for idx in range(6):
            converter = self.channel_converters[idx]
            if not converter:
                continue

            temp = self.ntc_widgets[idx][1].value()
            voltage = converter.temp2voltage(temp)

            self.hk.write_voltage(idx + 1, voltage)

            if abs(voltage - self.last_voltages[idx]) > 0.001:
                self.ntc_widgets[idx][3].setText(f"{temp:.1f}℃ → {voltage:.3f}V")
                self.last_voltages[idx] = voltage

    def update_output_immediately(self, idx):
        """立即更新单个通道输出（用于滑块拖动和自动模式）"""
        if not self.hk:
            return

        converter = self.channel_converters[idx]
        if not converter:
            return

        temp = self.ntc_widgets[idx][1].value()
        voltage = converter.temp2voltage(temp)
        self.hk.write_voltage(idx + 1, voltage)

    def reset_all(self):
        """复位所有通道到25℃"""
        for idx in range(6):
            self.current_t[idx] = 25.0
            self._update_ui_from_data(idx)
        self.last_voltages = [0.0] * 6

    def closeEvent(self, event):
        self.save_cfg()
        if self.hk:
            self.hk.close()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
