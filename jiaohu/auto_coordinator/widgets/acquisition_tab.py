import os
import sqlite3
import time
import json
import datetime
import logging
import traceback

import numpy as np
from collections import deque
from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QPushButton, QComboBox, QGroupBox, QGridLayout,
    QCheckBox, QMessageBox, QSplitter, QSpinBox, QFileDialog,
    QHeaderView, QDialog, QRadioButton, QTextEdit,
)
from PyQt5.QtCore import Qt, QTimer, QMutex, QMutexLocker
from PyQt5.QtGui import QColor, QFont

import pyqtgraph as pg
from pymodbus.client import ModbusSerialClient as ModbusClient
from pymodbus.exceptions import ModbusException

from serial.tools import list_ports

BASE_DIR = Path(__file__).parent.parent


class ModbusRTUClient:
    """Modbus RTU通信客户端，处理与硬件模块的通信"""

    def __init__(self, port=None, baudrate=9600, timeout=1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.client = None
        self.slave_address = 1
        self.data_format = 0
        self.sampling_interval = 50
        self.is_connected = False
        self.last_data = [0] * 18
        self.mutex = QMutex()
        self.communication_failure_count = 0

    def connect(self):
        """连接到Modbus设备"""
        with QMutexLocker(self.mutex):
            try:
                self.client = ModbusClient(
                    method='rtu',
                    port=self.port,
                    baudrate=self.baudrate,
                    timeout=self.timeout,
                    stopbits=1,
                    bytesize=8,
                    parity="N"
                )
                self.client.connect()
                self.is_connected = True
                self.communication_failure_count = 0
                logging.info(f"已连接到 {self.port}, 波特率 {self.baudrate}")
                return True
            except Exception as e:
                logging.error(f"连接错误: {e}")
                self.disconnect()
                return False

    def disconnect(self):
        """断开与Modbus设备的连接"""
        with QMutexLocker(self.mutex):
            if self.client and self.is_connected:
                try:
                    self.client.close()
                except Exception as e:
                    logging.error(f"关闭连接时出错: {e}")
                finally:
                    self.is_connected = False
                    logging.info("已断开连接")

    def read_voltage_data(self):
        """读取18路电压数据，使用真实的Modbus协议实现"""
        with QMutexLocker(self.mutex):
            if not self.is_connected:
                logging.warning("读取数据时未连接到设备")
                return [0] * 18

            try:
                if self.client.socket:
                    self.client.socket.read_all()

                retries = 5
                result = None
                for attempt in range(retries):
                    try:
                        result = self.client.read_input_registers(address=0x0000, count=18, slave=self.slave_address, timeout=2)
                        if not result.isError():
                            break
                        else:
                            logging.warning(f"Modbus读取错误 (尝试 {attempt + 1}/{retries}): {result}, 正在重试...")
                            time.sleep(0.5)
                    except Exception as e:
                        logging.error(f"Modbus读取异常 (尝试 {attempt + 1}/{retries}): {e}, 正在重试...")
                        time.sleep(0.5)

                if result is None or result.isError():
                    self.communication_failure_count += 1
                    logging.error(f"Modbus读取失败: {result}, 连续失败次数: {self.communication_failure_count}")
                    if self.communication_failure_count >= 3:
                        logging.warning("连续通信失败次数过多，尝试重新连接")
                        self.disconnect()
                        self.connect()
                    return self.last_data

                self.communication_failure_count = 0

                data = []
                for val in result.registers:
                    voltage = self._parse_value(val)
                    data.append(round(voltage, 3))
                self.last_data = data
                logging.debug(f"成功读取数据: {data}")
                return data
            except ModbusException as e:
                self.communication_failure_count += 1
                logging.error(f"Modbus通信异常: {e}, 连续失败次数: {self.communication_failure_count}")
                if self.communication_failure_count >= 3:
                    logging.warning("连续通信失败次数过多，尝试重新连接")
                    self.disconnect()
                    self.connect()
                return self.last_data

    def _parse_value(self, register_value):
        """解析寄存器值为实际电压值（固定为可变小数点格式）"""
        try:
            decimal_places = register_value // 10000
            value = register_value % 10000
            if decimal_places > 0:
                return value / (10 ** decimal_places)
            else:
                return value
        except Exception as e:
            logging.error(f"数据解析错误: {e}, 寄存器值: {register_value}")
            return 0.0

    def set_slave_address(self, address):
        """设置从站地址"""
        with QMutexLocker(self.mutex):
            if not self.is_connected:
                return False

            try:
                self.client.write_register(address=0x0032, value=address, slave=self.slave_address)
                self.slave_address = address
                logging.info(f"从站地址已设置为: {address}")
                return True
            except Exception as e:
                logging.error(f"设置地址错误: {e}")
                return False

    def set_baudrate(self, baudrate_index):
        """设置波特率"""
        with QMutexLocker(self.mutex):
            if not self.is_connected:
                return False

            try:
                self.client.write_register(address=0x0033, value=baudrate_index, slave=self.slave_address)
                logging.info(f"波特率已设置为: {baudrate_index}")
                return True
            except Exception as e:
                logging.error(f"设置波特率错误: {e}")
                return False

    def configure_module(self, baudrate_index=None, data_format=None):
        """配置模块参数"""
        with QMutexLocker(self.mutex):
            if not self.is_connected:
                return False

            try:
                if baudrate_index is not None:
                    self.client.write_register(address=0x0033, value=baudrate_index, slave=self.slave_address)
                    logging.info(f"波特率已设置为: {baudrate_index}")

                if data_format is not None:
                    self.client.write_register(address=0x003A, value=data_format, slave=self.slave_address)
                    logging.info(f"数据解析方式已设置为: {data_format}")

                return True
            except Exception as e:
                logging.error(f"模块配置错误: {e}")
                return False

    def perform_self_check(self):
        """执行模块自检"""
        with QMutexLocker(self.mutex):
            if not self.is_connected:
                logging.warning("模块自检失败: 设备未连接")
                return False

            try:
                result = self.client.read_holding_registers(address=0x0032, count=1, slave=self.slave_address)
                if result.isError():
                    logging.error("自检失败: 无法读取模块站号")
                    return False

                current_address = result.registers[0]
                logging.info(f"模块自检成功: 当前站号为 {current_address}")
                return True
            except Exception as e:
                logging.error(f"模块自检失败: {e}")
                return False

    def validate_frame(self, frame):
        """验证数据帧格式"""
        try:
            if len(frame) < 4:
                logging.error("数据帧长度不足")
                return False

            crc_calculated = self.calculate_crc(frame[:-2])
            crc_received = (frame[-1] << 8) | frame[-2]
            if crc_calculated != crc_received:
                logging.error(f"CRC 校验失败: 计算值 {crc_calculated}, 接收值 {crc_received}")
                return False

            data_start = 2
            data_length = frame[2]
            if data_length * 2 + 4 != len(frame):
                logging.error("数据长度不匹配")
                return False

            return True
        except Exception as e:
            logging.error(f"数据帧验证错误: {e}")
            return False

    def calculate_crc(self, data):
        """计算 Modbus RTU CRC 校验码"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        return crc


class DataAcquisitionThread:
    def __init__(self, modbus_client):
        self.modbus_client = modbus_client
        self.running = False
        self.timer = QTimer()
        self.timer.timeout.connect(self.collect_data)
        self.mutex = QMutex()
        self.data_updated = None

    def set_callback(self, callback):
        self.data_updated = callback

    def start(self):
        with QMutexLocker(self.mutex):
            self.running = True
            self.timer.start(self.modbus_client.sampling_interval)

    def stop(self):
        with QMutexLocker(self.mutex):
            if self.running:
                self.running = False
                self.timer.stop()
                logging.info("数据采集线程已停止")

    def collect_data(self):
        with QMutexLocker(self.mutex):
            if not self.running or not self.modbus_client.is_connected:
                return
        try:
            data = self.modbus_client.read_voltage_data()
            current_time = time.time()
            if self.data_updated:
                self.data_updated(current_time, data)
        except Exception as e:
            logging.error(f"数据采集错误: {e}")
            traceback.print_exc()


class DataStorageThread:
    def __init__(self, data_buffer, db_conn):
        self.data_buffer = data_buffer
        self.db_conn = db_conn
        self.running = True
        self.batch_size = 100
        self._timer = QTimer()

    def start(self):
        self._timer.timeout.connect(self._process_batch)
        self._timer.start(100)

    def _process_batch(self):
        if not self.running:
            self._timer.stop()
            return
        batch = []
        while self.data_buffer and len(batch) < self.batch_size:
            try:
                timestamp, data = self.data_buffer.popleft()
                batch.append((timestamp, data))
            except IndexError:
                break
        if batch:
            self.store_batch_to_database(batch)

    def store_batch_to_database(self, batch):
        try:
            cursor = self.db_conn.cursor()
            cursor.executemany(
                "INSERT INTO voltage_data (timestamp, channel1, channel2, channel3, channel4, channel5, channel6, channel7, channel8, channel9, channel10, channel11, channel12, channel13, channel14, channel15, channel16, channel17, channel18) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [([datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')[:-3]] + d) for ts, d in batch])
            self.db_conn.commit()
        except sqlite3.OperationalError as e:
            logging.error(f"批量数据存储失败: {e}")
            self.db_conn.rollback()

    def stop(self):
        self.running = False
        self._timer.stop()


class ThresholdConfigDialog(QDialog):
    """阈值配置对话框"""

    def __init__(self, thresholds, channel_names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("阈值配置")
        self.setMinimumWidth(800)
        self.thresholds = thresholds.copy()
        self.channel_names = channel_names.copy()

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        self.table = QTableWidget(1, 18)
        self.table.setHorizontalHeaderLabels(self.channel_names)
        self.table.setVerticalHeaderLabels(["阈值(V)"])

        for col in range(18):
            threshold_item = QTableWidgetItem(f"{self.thresholds[col]:.3f}")
            threshold_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(0, col, threshold_item)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self.accept)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(self.apply_btn)
        btn_layout.addWidget(self.cancel_btn)

        main_layout.addWidget(self.table)
        main_layout.addLayout(btn_layout)

    def get_thresholds(self):
        new_thresholds = []
        for col in range(18):
            try:
                threshold = float(self.table.item(0, col).text())
                new_thresholds.append(threshold)
            except (ValueError, AttributeError):
                new_thresholds.append(self.thresholds[col])
        return new_thresholds


class ChannelConfigDialog(QDialog):
    """通道名称配置对话框"""

    def __init__(self, channel_names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("通道名称配置")
        #self.setMinimumWidth(400)
        self.resize(500, 630)
        self.channel_names = channel_names.copy()

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        self.table = QTableWidget(18, 3)
        self.table.setHorizontalHeaderLabels(["通道编号", "当前名称", "新名称"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        for i in range(18):
            row = i
            col = 0
            channel_number_item = QTableWidgetItem(f"通道{i + 1}")
            channel_number_item.setTextAlignment(Qt.AlignCenter)
            channel_number_item.setFlags(channel_number_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, col, channel_number_item)

            col = 1
            current_name_item = QTableWidgetItem(self.channel_names[i])
            current_name_item.setTextAlignment(Qt.AlignCenter)
            current_name_item.setFlags(current_name_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, col, current_name_item)

            col = 2
            new_name_item = QTableWidgetItem(self.channel_names[i])
            new_name_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, col, new_name_item)

        self.table.setStyleSheet("""
            QTableWidget {
                gridline-color: #e0e0e0;
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            QTableWidget::item {
                padding: 8px;
            }
        """)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self.accept)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(self.apply_btn)
        btn_layout.addWidget(self.cancel_btn)

        main_layout.addWidget(self.table)
        main_layout.addLayout(btn_layout)

    def get_channel_names(self):
        new_names = []
        for row in range(18):
            col = 2
            try:
                item = self.table.item(row, col)
                name = item.text() if item else f"通道{row + 1}"
                new_names.append(name)
            except (ValueError, AttributeError):
                new_names.append(self.channel_names[row])
        return new_names


class EventDisplayModeDialog(QDialog):
    """事件显示方式配置对话框"""

    def __init__(self, current_mode, parent=None):
        super().__init__(parent)
        self.setWindowTitle("事件显示方式")
        self.setMinimumWidth(300)
        self.current_mode = current_mode

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        self.mode0_radio = QRadioButton("始终显示最新事件")
        self.mode1_radio = QRadioButton("保持当前滚动位置")

        if self.current_mode == 0:
            self.mode0_radio.setChecked(True)
        else:
            self.mode1_radio.setChecked(True)

        main_layout.addWidget(self.mode0_radio)
        main_layout.addWidget(self.mode1_radio)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self.accept)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(self.apply_btn)
        btn_layout.addWidget(self.cancel_btn)

        main_layout.addLayout(btn_layout)

    def get_selected_mode(self):
        if self.mode0_radio.isChecked():
            return 0
        else:
            return 1


class PlotModeDialog(QDialog):
    """图表显示模式配置对话框"""

    def __init__(self, current_mode, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图表显示模式")
        self.setMinimumWidth(300)
        self.current_mode = current_mode

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        self.mode0_radio = QRadioButton("实时更新模式")
        self.mode1_radio = QRadioButton("拖动模式")

        if self.current_mode == 0:
            self.mode0_radio.setChecked(True)
        else:
            self.mode1_radio.setChecked(True)

        main_layout.addWidget(self.mode0_radio)
        main_layout.addWidget(self.mode1_radio)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self.accept)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(self.apply_btn)
        btn_layout.addWidget(self.cancel_btn)

        main_layout.addLayout(btn_layout)

    def get_selected_mode(self):
        if self.mode0_radio.isChecked():
            return 0
        else:
            return 1


class EventNoteDialog(QDialog):
    """事件备注对话框"""

    def __init__(self, note="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加备注")
        self.note = note

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        self.note_edit = QTextEdit(self.note)
        main_layout.addWidget(self.note_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self.accept)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(self.apply_btn)
        btn_layout.addWidget(self.cancel_btn)

        main_layout.addLayout(btn_layout)

    def get_note(self):
        return self.note_edit.toPlainText()


class AcquisitionTab(QWidget):
    def __init__(self, db_path=None):
        super().__init__()

        self.config_file = str(BASE_DIR / "config.json")
        self.db_file = db_path if db_path else str(BASE_DIR / "voltage_data.db")
        # ----------------------------------------

        self.modbus_client = ModbusRTUClient(baudrate=9600, timeout=1.0)
        self.acquisition_thread = None
        self.storage_thread = None
        self.data_buffer = deque(maxlen=1000)
        self.ui_update_timer = QTimer()

        self.thresholds = [5.0] * 18
        self.event_records = []
        self.current_event = None
        self.last_event_channels = set()

        self.event_timer = QTimer(self)
        self.event_timer.setInterval(10)
        self.event_timer.timeout.connect(self.update_event_duration)
        self.event_start_time = None
        self.current_duration = 0.0
        self.is_timing = False
        self.is_quiet = True
        self.quiet_start_time = None

        self.time_history = deque(maxlen=10000)
        self.data_history = [deque(maxlen=10000) for _ in range(18)]
        self.ui_update_interval = 10
        self.max_history_points = 10000

        # 默认通道名，将在 load_config 中被覆盖
        self.channel_names = [f"通道{i + 1}" for i in range(18)]
        self.event_display_mode = 0
        self.scroll_position = 0
        self.plot_mode = 0

        # 初始化顺序
        self.init_database()
        self.load_config()          # 先加载配置
        self.init_ui()              # 再构建界面
        self.init_connections()

        self.current_data = [0.0] * 18
        self.init_plot()

        self.ui_update_timer.timeout.connect(self.update_ui_from_buffer)
        self.ui_update_timer.start(self.ui_update_interval)

        self.storage_interval = 60000
        self.storage_timer = QTimer()
        self.storage_timer.timeout.connect(self.start_storage_thread)
        self.storage_timer.start(self.storage_interval)

        if self.modbus_client.is_connected:
            self.modbus_client.perform_self_check()

    def init_database(self):
        """初始化数据库"""
        try:
            self.db_conn = sqlite3.connect(self.db_file, check_same_thread=False)
            self.db_conn.execute('PRAGMA journal_mode = WAL')
            self.db_conn.execute('PRAGMA synchronous = NORMAL')
            self.db_conn.execute('PRAGMA cache_size = 10000')
            self.db_conn.execute('PRAGMA temp_store = MEMORY')

            # 创建电压数据表
            self.db_conn.execute('''
                CREATE TABLE IF NOT EXISTS voltage_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME,
                    channel1 REAL,
                    channel2 REAL,
                    channel3 REAL,
                    channel4 REAL,
                    channel5 REAL,
                    channel6 REAL,
                    channel7 REAL,
                    channel8 REAL,
                    channel9 REAL,
                    channel10 REAL,
                    channel11 REAL,
                    channel12 REAL,
                    channel13 REAL,
                    channel14 REAL,
                    channel15 REAL,
                    channel16 REAL,
                    channel17 REAL,
                    channel18 REAL
                )
            ''')

            # 创建事件表
            self.db_conn.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME,
                    description TEXT,
                    duration REAL,
                    note TEXT
                )
            ''')

            self.db_conn.commit()
            logging.info("数据库初始化成功")

        except sqlite3.OperationalError as e:
            logging.error(f"数据库初始化失败: {e}")

    def init_ui(self):
        """初始化用户界面"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # 创建工具栏按钮
        self.create_toolbar(main_layout)

        # 控制栏
        control_widget = QWidget()
        control_widget.setMaximumHeight(100)
        control_layout = QHBoxLayout(control_widget)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(10)

        # 连接控制
        connection_group = QGroupBox("连接设置")
        connection_group.setMaximumHeight(60)
        connection_layout = QHBoxLayout()
        connection_layout.setContentsMargins(5, 5, 5, 5)

        self.port_combo = QComboBox()
        self.port_combo.addItem("选择串口")
        ports = self.scan_available_ports()
        for port in ports:
            self.port_combo.addItem(port)

        self.baudrate_combo = QComboBox()
        baudrates = ["4800", "9600", "14400", "19200", "38400", "56000", "57600", "115200"]
        for idx, baud in enumerate(baudrates):
            self.baudrate_combo.addItem(baud)
        self.baudrate_combo.setCurrentIndex(1)

        self.connect_btn = QPushButton("连接")
        self.connect_btn.setObjectName("btn_success")
        self.connect_btn.setMinimumWidth(80)
        self.connect_btn.setMaximumHeight(30)

        self.address_label = QLabel("从站地址: 1")
        self.address_label.setMinimumWidth(100)
        self.address_label.setAlignment(Qt.AlignCenter)

        connection_layout.addWidget(QLabel("串口:"))
        connection_layout.addWidget(self.port_combo)
        connection_layout.addWidget(QLabel("波特率:"))
        connection_layout.addWidget(self.baudrate_combo)
        connection_layout.addWidget(self.connect_btn)
        connection_layout.addWidget(self.address_label)

        connection_group.setLayout(connection_layout)

        # 采样控制
        sample_group = QGroupBox("采样控制")
        sample_group.setMaximumHeight(60)
        sample_layout = QHBoxLayout()
        sample_layout.setContentsMargins(5, 5, 5, 5)

        self.sample_interval_label = QLabel("采样间隔:")
        self.sample_spinbox = QSpinBox()
        self.sample_spinbox.setRange(10, 1000)
        self.sample_spinbox.setValue(self.modbus_client.sampling_interval)
        self.sample_spinbox.setSuffix(" ms")
        self.sample_spinbox.setSingleStep(10)
        self.sample_spinbox.valueChanged.connect(self.update_sampling_interval)

        self.sample_interval_display = QLabel(f"{self.modbus_client.sampling_interval} ms")
        self.sample_interval_display.setMinimumWidth(60)
        self.sample_interval_display.setAlignment(Qt.AlignCenter)

        self.start_btn = QPushButton("开始采样")
        self.start_btn.setObjectName("btn_success")
        self.start_btn.setMinimumWidth(80)
        self.start_btn.setMaximumHeight(30)
        self.start_btn.setEnabled(False)

        self.stop_btn = QPushButton("停止采样")
        self.stop_btn.setObjectName("btn_danger")
        self.stop_btn.setMinimumWidth(80)
        self.stop_btn.setMaximumHeight(30)
        self.stop_btn.setEnabled(False)

        # 事件导出按钮
        self.export_events_btn = QPushButton("事件导出")
        self.export_events_btn.setObjectName("btn_info")
        self.export_events_btn.setMinimumWidth(80)
        self.export_events_btn.setMaximumHeight(30)
        self.export_events_btn.clicked.connect(self.export_events)

        sample_layout.addWidget(self.sample_interval_label)
        sample_layout.addWidget(self.sample_spinbox)
        sample_layout.addWidget(self.sample_interval_display)
        sample_layout.addWidget(self.start_btn)
        sample_layout.addWidget(self.stop_btn)
        sample_layout.addWidget(self.export_events_btn)

        sample_group.setLayout(sample_layout)

        # 将组框添加到控制栏布局
        control_layout.addWidget(connection_group)
        control_layout.addWidget(sample_group)

        main_layout.addWidget(control_widget)

        # 数据显示区域
        display_splitter = QSplitter(Qt.Horizontal)

        # 左侧：事件记录窗口
        event_widget = QWidget()
        event_layout = QVBoxLayout(event_widget)

        event_title = QLabel("事件记录")
        event_title.setFont(QFont("SimHei", 10, QFont.Bold))
        event_layout.addWidget(event_title)

        # 事件记录表格（调整为5列，新增备注列）
        self.event_table = QTableWidget()
        self.event_table.setColumnCount(5)
        self.event_table.setHorizontalHeaderLabels(["序号", "状态描述", "开始时间", "持续时间(秒)", "备注"])
        self.event_table.horizontalHeader().setStretchLastSection(True)
        self.event_table.setEditTriggers(QTableWidget.NoEditTriggers)

        event_layout.addWidget(self.event_table)

        # 控制按钮
        btn_layout = QHBoxLayout()
        clear_btn = QPushButton("清除记录")
        clear_btn.setObjectName("btn_warning")
        clear_btn.clicked.connect(self.clear_event_records)
        add_note_btn = QPushButton("添加备注")
        add_note_btn.clicked.connect(self.add_event_note)
        btn_layout.addWidget(clear_btn)
        btn_layout.addWidget(add_note_btn)

        event_layout.addLayout(btn_layout)

        display_splitter.addWidget(event_widget)

        # 右侧：实时电压数据(2行) + 波形图
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # 实时电压数据(4行9列)
        voltage_widget = QWidget()
        voltage_layout = QVBoxLayout(voltage_widget)

        voltage_title = QLabel("实时电压数据(单位: V)")
        voltage_title.setFont(QFont("SimHei", 10, QFont.Bold))
        voltage_layout.addWidget(voltage_title)

        # 创建4行9列的表格
        self.data_table = QTableWidget(4, 9)

        # 设置水平表头
        self.data_table.setHorizontalHeaderLabels([f"{i + 1}" for i in range(9)])

        # 隐藏垂直表头
        self.data_table.verticalHeader().setVisible(False)

        # 初始化表格内容
        # 第1行：通道1-9
        for col in range(9):
            channel_item = QTableWidgetItem(self.channel_names[col])
            channel_item.setTextAlignment(Qt.AlignCenter)
            channel_item.setFont(QFont("SimHei", 10))
            self.data_table.setItem(0, col, channel_item)

            voltage_item = QTableWidgetItem("0.000")
            voltage_item.setTextAlignment(Qt.AlignCenter)
            self.data_table.setItem(1, col, voltage_item)

        # 第3行：通道10-18
        for col in range(9):
            channel_item = QTableWidgetItem(self.channel_names[col + 9])
            channel_item.setTextAlignment(Qt.AlignCenter)
            channel_item.setFont(QFont("SimHei", 10))
            self.data_table.setItem(2, col, channel_item)

            voltage_item = QTableWidgetItem("0.000")
            voltage_item.setTextAlignment(Qt.AlignCenter)
            self.data_table.setItem(3, col, voltage_item)

        # 设置列宽为自动适应
        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        # 表格样式设置
        self.data_table.horizontalHeader().setVisible(False)

        voltage_layout.addWidget(self.data_table)

        # 设置实时电压数据显示窗口的最小高度
        voltage_widget.setMinimumHeight(100)

        right_layout.addWidget(voltage_widget)

        # 波形图
        plot_widget = QWidget()
        plot_layout = QVBoxLayout(plot_widget)

        plot_title = QLabel("电压实时波形")
        plot_title.setFont(QFont("SimHei", 10, QFont.Bold))
        plot_layout.addWidget(plot_title)

        # 使用PyQtGraph的PlotWidget
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setTitle("电压实时波形")
        self.plot_widget.setLabel('left', "电压 (V)")
        self.plot_widget.setLabel('bottom', "时间")
        self.plot_widget.showAxis('bottom', True)
        self.plot_widget.showAxis('left', True)
        self.plot_widget.enableAutoRange('x', True)
        self.plot_widget.enableAutoRange('y', False)
        self.plot_widget.setMouseEnabled(x=True, y=False)
        self.plot_widget.addLegend()
        # 设置背景颜色为白色
        self.plot_widget.setBackground('w')

        # 创建时间格式的X轴
        self.date_axis = pg.DateAxisItem(orientation='bottom')
        self.date_axis.setLabel('时间')
        self.plot_widget.setAxisItems({"bottom": self.date_axis})

        # 添加鼠标交互功能
        self.plot_widget.setMouseTracking(True)
        self.plot_widget.scene().sigMouseMoved.connect(self.show_coordinate)

        # 添加导航控件
        self.zoom_in_btn = QPushButton("放大")
        self.zoom_in_btn.clicked.connect(self.zoom_in)
        self.zoom_out_btn = QPushButton("缩小")
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        self.reset_view_btn = QPushButton("复位")
        self.reset_view_btn.clicked.connect(self.reset_view)

        nav_layout = QHBoxLayout()
        nav_layout.addWidget(self.zoom_in_btn)
        nav_layout.addWidget(self.zoom_out_btn)
        nav_layout.addWidget(self.reset_view_btn)

        plot_layout.addWidget(self.plot_widget)
        plot_layout.addLayout(nav_layout)

        # 通道选择
        channel_group = QGroupBox("选择显示通道")
        channel_layout = QGridLayout()

        self.channel_checkboxes = []
        for i in range(18):
            cb = QCheckBox(self.channel_names[i])
            if i < 6:
                cb.setChecked(True)
            channel_layout.addWidget(cb, i // 6, i % 6)
            self.channel_checkboxes.append(cb)
            cb.stateChanged.connect(self.request_plot_update)

        channel_group.setLayout(channel_layout)
        plot_layout.addWidget(channel_group)

        plot_widget.setMinimumHeight(200)
        right_layout.addWidget(plot_widget)

        display_splitter.addWidget(right_widget)
        display_splitter.setSizes([550, 600])

        main_layout.addWidget(display_splitter)

        # 底部状态栏
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setMinimumHeight(22)
        self.lbl_status.setStyleSheet("background: #f0f0f0; padding: 2px 8px;")
        main_layout.addWidget(self.lbl_status)

    def create_toolbar(self, parent_layout):
        toolbar_widget = QWidget()
        toolbar_layout = QHBoxLayout(toolbar_widget)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(4)

        btn_threshold = QPushButton("阈值设置")
        btn_threshold.clicked.connect(self.configure_thresholds)
        btn_channel = QPushButton("通道名称配置")
        btn_channel.clicked.connect(self.configure_channel_names)
        btn_event_display = QPushButton("事件显示方式")
        btn_event_display.clicked.connect(self.configure_event_display)
        btn_plot_mode = QPushButton("图表显示模式")
        btn_plot_mode.clicked.connect(self.configure_plot_mode)
        btn_time_format = QPushButton("切换时间格式")
        btn_time_format.clicked.connect(self.toggle_time_format)
        btn_export = QPushButton("导出数据")
        btn_export.clicked.connect(self.export_data)
        btn_about = QPushButton("关于")
        btn_about.clicked.connect(self.show_about)

        toolbar_layout.addWidget(btn_threshold)
        toolbar_layout.addWidget(btn_channel)
        toolbar_layout.addWidget(btn_event_display)
        toolbar_layout.addWidget(btn_plot_mode)
        toolbar_layout.addWidget(btn_time_format)
        toolbar_layout.addWidget(btn_export)
        toolbar_layout.addWidget(btn_about)
        toolbar_layout.addStretch()

        parent_layout.addWidget(toolbar_widget)

    def init_connections(self):
        """初始化信号与槽连接"""
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.start_btn.clicked.connect(self.start_acquisition)
        self.stop_btn.clicked.connect(self.stop_acquisition)
        self.sample_spinbox.valueChanged.connect(self.update_sampling_interval)

    def set_status(self, msg):
        self.lbl_status.setText(msg)
        logging.info(msg)

    def toggle_connection(self):
        """切换连接状态"""
        if self.modbus_client.is_connected:
            self.modbus_client.disconnect()
            self.connect_btn.setText("连接")
            self.start_btn.setEnabled(False)
            self.set_status("已断开连接")
        else:
            selected_port = self.port_combo.currentText()
            if selected_port == "选择串口":
                QMessageBox.warning(self, "警告", "请先选择串口")
                return

            self.modbus_client.port = selected_port
            self.modbus_client.baudrate = int(self.baudrate_combo.currentText())

            if self.modbus_client.connect():
                self.connect_btn.setText("断开")
                self.start_btn.setEnabled(True)
                self.set_status(f"已连接到 {selected_port}, 波特率 {self.modbus_client.baudrate}")
                self.modbus_client.perform_self_check()
            else:
                QMessageBox.critical(self, "错误", f"无法连接到 {selected_port}")

    def scan_available_ports(self):
        """扫描可用的串口"""
        available_ports = []
        try:
            ports = list(serial.tools.list_ports.comports())
            for port in ports:
                available_ports.append(port.device)
        except Exception as e:
            logging.error(f"扫描串口时出错: {e}")
        return available_ports

    def start_acquisition(self):
        """开始数据采集"""
        if not self.acquisition_thread or not self.acquisition_thread.isRunning():
            self.acquisition_thread = DataAcquisitionThread(self.modbus_client)
            self.acquisition_thread.data_updated.connect(self.handle_data_update)
            self.acquisition_thread.start()

            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.set_status("数据采集已开始")

    def stop_acquisition(self):
        """停止数据采集"""
        if self.acquisition_thread and (self.acquisition_thread.isRunning() or self.acquisition_thread.isFinished()):
            logging.info("正在停止数据采集线程...")
            self.acquisition_thread.stop()
            self.acquisition_thread = None

            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.set_status("数据采集已停止")
            logging.info("数据采集已停止，UI按钮状态已更新")

    def update_sampling_interval(self, value):
        """更新采样间隔"""
        self.modbus_client.sampling_interval = value
        self.sample_interval_display.setText(f"{value} ms")
        self.set_status(f"采样间隔已更新为 {value} ms")

        if self.acquisition_thread:
            self.acquisition_thread.timer.setInterval(value)

    def handle_data_update(self, timestamp, data):
        """处理数据更新"""
        try:
            self.data_buffer.append((timestamp, data))
        except Exception as e:
            logging.error(f"数据缓冲区操作错误: {e}")

    def update_ui_from_buffer(self):
        """从缓冲区更新UI"""
        if not self.data_buffer:
            return

        timestamp, data = self.data_buffer[-1]

        self.current_data = data.copy()

        # 更新历史数据
        self.time_history.append(timestamp)
        for i in range(18):
            self.data_history[i].append(data[i])

        # 检查事件
        self.check_events(data, timestamp)

        # 更新实时数据表格
        self.update_data_table()

        # 更新图表
        self.update_plot()

    def update_data_table(self):
        """更新实时数据表格"""
        for col in range(9):
            # 更新通道1-9
            value = self.current_data[col]
            item = self.data_table.item(1, col)
            item.setText(f"{value:.3f}")

            if value > self.thresholds[col]:
                item.setBackground(QColor(255, 200, 200))
            else:
                item.setBackground(QColor(255, 255, 255))

            # 更新通道10-18
            value = self.current_data[col + 9]
            item = self.data_table.item(3, col)
            item.setText(f"{value:.3f}")

            if value > self.thresholds[col + 9]:
                item.setBackground(QColor(255, 200, 200))
            else:
                item.setBackground(QColor(255, 255, 255))

    def update_plot(self):
        """更新波形图"""
        if not self.time_history:
            return

        timestamps = list(self.time_history)
        all_data = [item for sublist in self.data_history for item in sublist]
        if all_data:
            min_voltage = min(all_data) - 1
            max_voltage = max(all_data) + 1
            self.plot_widget.setYRange(min_voltage, max_voltage)

        for i in range(18):
            if self.channel_checkboxes[i].isChecked():
                self.curves[i].setData(timestamps, list(self.data_history[i]))

                threshold = self.thresholds[i]
                self.threshold_lines[i].setData([timestamps[0], timestamps[-1]], [threshold, threshold])
            else:
                self.curves[i].setData([], [])
                self.threshold_lines[i].setData([], [])

        # 根据图表显示模式调整显示范围
        if self.plot_mode == 0:  # 实时更新模式
            if timestamps:
                self.plot_widget.setXRange(timestamps[-1] - 60, timestamps[-1])  # 显示最近60秒的数据
        else:  # 拖动模式
            # 如果处于拖动模式，不强制调整X轴范围
            pass

    def init_plot(self):
        """初始化图表"""
        self.plot_widget.clear()
        self.plot_widget.setTitle("电压实时波形")
        self.plot_widget.setLabel('left', "电压 (V)")
        self.plot_widget.setLabel('bottom', "时间")
        self.plot_widget.showAxis('bottom', True)
        self.plot_widget.showAxis('left', True)
        self.plot_widget.setMouseEnabled(x=True, y=False)
        self.plot_widget.addLegend()
        # 设置背景颜色为白色
        self.plot_widget.setBackground('w')

        # 创建时间格式的X轴
        self.date_axis = pg.DateAxisItem(orientation='bottom')
        self.date_axis.setLabel('时间')
        self.plot_widget.setAxisItems({"bottom": self.date_axis})

        colors = [
            (0, 0, 255),
            (0, 255, 0),
            (255, 0, 0),
            (0, 255, 255),
            (255, 0, 255),
            (255, 255, 0),
            (0, 0, 0),
            (70, 130, 180),
            (255, 140, 0),
            (34, 139, 34),
            (178, 34, 34),
            (147, 112, 219),
            (165, 42, 42),
            (255, 192, 203),
            (169, 169, 169),
            (128, 128, 0),
            (0, 139, 139),
            (128, 0, 128)
        ]

        self.curves = []
        self.threshold_lines = []
        for i in range(18):
            color = colors[i % len(colors)]
            self.curves.append(self.plot_widget.plot(pen=color, name=self.channel_names[i]))
            self.threshold_lines.append(
                self.plot_widget.plot([], [], pen=pg.mkPen(color=color, style=Qt.DashLine)))

        # 根据图表模式初始化自动范围
        if self.plot_mode == 0:  # 实时更新模式
            self.plot_widget.enableAutoRange('x', True)
            self.plot_widget.enableAutoRange('y', False)
        else:  # 拖动模式
            self.plot_widget.enableAutoRange('x', False)
            self.plot_widget.enableAutoRange('y', False)

    def request_plot_update(self):
        """请求更新图表（当通道选择变化时）"""
        self.update_plot()

    def check_events(self, data, timestamp):
        """检查并记录事件"""
        current_channels = set()
        has_event = False

        for i, value in enumerate(data):
            if value > self.thresholds[i]:
                current_channels.add(i)
                has_event = True

        time_str = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')[:-3]

        if has_event:
            if (not self.current_event) or (current_channels != self.last_event_channels):
                if self.current_event:
                    self.stop_event_timing()
                    self.current_event['duration'] = self.current_duration
                    self.event_records.append(self.current_event.copy())
                    self.update_event_table()
                    self.record_event(self.current_event['description'], self.current_event['start_time'], self.current_duration, self.current_event.get('note', ''))

                channel_names = ", ".join([self.channel_names[i] for i in current_channels])
                description = f"{channel_names} "
                self.current_event = {
                    'id': len(self.event_records) + 1,
                    'description': description,
                    'start_time': time_str,
                    'start_timestamp': timestamp,
                    'channels': current_channels.copy(),
                    'duration': 0.0,
                    'note': ''
                }
                self.last_event_channels = current_channels.copy()
                self.is_quiet = False
                self.start_event_timing()
            else:
                pass
        else:
            if not self.is_quiet:
                if self.current_event:
                    self.stop_event_timing()
                    self.current_event['duration'] = self.current_duration
                    self.event_records.append(self.current_event.copy())
                    self.update_event_table()
                    self.record_event(self.current_event['description'], self.current_event['start_time'], self.current_duration, self.current_event.get('note', ''))

                self.is_quiet = True
                self.quiet_start_time = timestamp
                description = "系统正常"
                self.current_event = {
                    'id': len(self.event_records) + 1,
                    'description': description,
                    'start_time': time_str,
                    'start_timestamp': timestamp,
                    'channels': set(),
                    'duration': 0.0,
                    'note': ''
                }
                self.last_event_channels = set()
                self.start_event_timing()
            else:
                if self.is_timing:
                    self.current_duration = timestamp - self.quiet_start_time
                    self.update_event_table()

    def start_event_timing(self):
        """启动事件计时器"""
        if not self.is_timing:
            self.event_start_time = time.time()
            self.current_duration = 0.0
            self.is_timing = True
            self.event_timer.start()

    def stop_event_timing(self):
        """停止事件计时器"""
        if self.is_timing:
            self.event_timer.stop()
            self.is_timing = False

    def update_event_duration(self):
        """更新事件持续时间"""
        if self.is_timing and self.current_event:
            elapsed_time = time.time() - self.event_start_time
            self.current_duration = elapsed_time
            self.current_event['duration'] = elapsed_time
            self.update_event_table()

    def update_event_table(self):
        """更新事件表格"""
        self.event_table.setRowCount(len(self.event_records) + (1 if self.current_event else 0))

        if self.event_display_mode == 1:
            self.scroll_position = self.event_table.verticalScrollBar().value()

        for row, event in enumerate(self.event_records):
            id_item = QTableWidgetItem(str(event['id']))
            id_item.setTextAlignment(Qt.AlignCenter)
            self.event_table.setItem(row, 0, id_item)

            desc_item = QTableWidgetItem(event['description'])
            desc_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.event_table.setItem(row, 1, desc_item)

            start_time_item = QTableWidgetItem(event['start_time'])
            start_time_item.setTextAlignment(Qt.AlignCenter)
            self.event_table.setItem(row, 2, start_time_item)

            if self.time_format == "hms":
                total_seconds = event['duration']
                hours = int(total_seconds // 3600)
                minutes = int((total_seconds % 3600) // 60)
                seconds = total_seconds % 60
                if hours > 0:
                    duration_str = f"{hours}:{minutes:02d}:{seconds:05.2f}"
                else:
                    duration_str = f"{minutes}:{seconds:05.2f}"
            else:
                duration_str = f"{event['duration']:.1f}"
            duration_item = QTableWidgetItem(duration_str)
            duration_item.setTextAlignment(Qt.AlignCenter)
            self.event_table.setItem(row, 3, duration_item)

            note_item = QTableWidgetItem(event.get('note', ''))
            note_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.event_table.setItem(row, 4, note_item)

        if self.current_event:
            row = len(self.event_records)
            id_item = QTableWidgetItem(str(self.current_event['id']))
            id_item.setTextAlignment(Qt.AlignCenter)
            self.event_table.setItem(row, 0, id_item)

            desc_item = QTableWidgetItem(self.current_event['description'])
            desc_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.event_table.setItem(row, 1, desc_item)

            start_time_item = QTableWidgetItem(self.current_event['start_time'])
            start_time_item.setTextAlignment(Qt.AlignCenter)
            self.event_table.setItem(row, 2, start_time_item)

            #duration_item = QTableWidgetItem(f"{self.current_event['duration']:.1f}")
            total_seconds = self.current_event['duration']
            if self.time_format == "hms":
                hours = int(total_seconds // 3600)
                minutes = int((total_seconds % 3600) // 60)
                seconds = total_seconds % 60
                duration_str = f"{hours:02d}:{minutes:02d}:{seconds:05.2f}"
            else:
                duration_str = f"{total_seconds:.1f}"
            duration_item = QTableWidgetItem(duration_str)
            duration_item.setTextAlignment(Qt.AlignCenter)
            self.event_table.setItem(row, 3, duration_item)

            note_item = QTableWidgetItem(self.current_event.get('note', ''))
            note_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.event_table.setItem(row, 4, note_item)

            for col in range(5):
                self.event_table.item(row, col).setBackground(QColor(220, 255, 220))

        self.event_table.resizeColumnsToContents()

        if self.event_display_mode == 0:
            self.event_table.scrollToBottom()
        else:
            self.event_table.verticalScrollBar().setValue(self.scroll_position)

    def start_storage_thread(self):
        """启动数据存储线程"""
        if not self.storage_thread or not self.storage_thread.running:
            self.storage_thread = DataStorageThread(deque(self.data_buffer), self.db_conn)
            self.storage_thread.start()

    def store_data_to_database(self):
        """将数据存储到数据库"""
        if self.data_buffer.empty():
            return

        cursor = self.db_conn.cursor()
        batch_size = 100
        count = 0

        data_list = []
        while count < batch_size and not self.data_buffer.empty():
            try:
                timestamp, data = self.data_buffer.popleft()
                timestamp_str = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')[:-3]
                data_list.append([timestamp_str] + data)
                count += 1
            except IndexError:
                break

        if data_list:
            try:
                cursor.executemany('''
                    INSERT INTO voltage_data (timestamp, channel1, channel2, channel3, channel4, channel5, 
                                              channel6, channel7, channel8, channel9, channel10, channel11, 
                                              channel12, channel13, channel14, channel15, channel16, channel17, 
                                              channel18)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', data_list)
                self.db_conn.commit()
                logging.info(f"已存储 {count} 条数据到数据库")
            except sqlite3.OperationalError as e:
                logging.error(f"数据存储失败: {e}")
                self.db_conn.rollback()

    def record_event(self, description, start_time, duration, note=''):
        """记录事件到数据库"""
        try:
            cursor = self.db_conn.cursor()
            cursor.execute('''
                INSERT INTO events (timestamp, description, duration, note)
                VALUES (?, ?, ?, ?)
            ''', (start_time, description, duration, note))
            self.db_conn.commit()
        except sqlite3.OperationalError as e:
            logging.error(f"事件记录失败: {e}")
            self.db_conn.rollback()

    def clear_event_records(self):
        """清除所有事件记录"""
        self.event_records = []
        self.current_event = None
        self.event_table.setRowCount(0)

        try:
            cursor = self.db_conn.cursor()
            cursor.execute("DELETE FROM events")
            self.db_conn.commit()
        except sqlite3.OperationalError as e:
            logging.error(f"清除事件记录失败: {e}")

    def export_events(self):
        """导出事件记录为Excel文档"""
        if not self.event_records and not self.current_event:
            QMessageBox.warning(self, "警告", "没有事件可导出")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "导出事件记录", "", "Excel文件 (*.xlsx);;所有文件 (*)")

        if not filename:
            return

        try:
            events_to_export = self.event_records.copy()
            if self.current_event:
                events_to_export.append(self.current_event.copy())

            import pandas as pd
            data = []
            for event in events_to_export:
                data.append([
                    event['id'],
                    event['description'],
                    event['start_time'],
                    event['duration'],
                    event.get('note', '')
                ])

            df = pd.DataFrame(data, columns=["序号", "状态描述", "开始时间", "持续时间(秒)", "备注"])
            df.to_excel(filename, index=False, engine='openpyxl')

            QMessageBox.information(self, "成功", f"事件记录已成功导出到 {filename}")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出事件记录失败: {str(e)}")

    def add_event_note(self):
        """添加事件备注"""
        selected_row = self.event_table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "警告", "请先选择一个事件")
            return

        event_id = int(self.event_table.item(selected_row, 0).text())

        target_event = None
        for event in self.event_records:
            if event['id'] == event_id:
                target_event = event
                break

        if not target_event and self.current_event and self.current_event['id'] == event_id:
            target_event = self.current_event

        if not target_event:
            QMessageBox.warning(self, "警告", "未找到选中的事件")
            return

        note_dialog = EventNoteDialog(target_event.get('note', ''), self)
        if note_dialog.exec_():
            new_note = note_dialog.get_note()
            target_event['note'] = new_note
            self.update_event_table()

            try:
                cursor = self.db_conn.cursor()
                cursor.execute('''
                    UPDATE events SET note = ? WHERE id = ?
                ''', (new_note, event_id))
                self.db_conn.commit()
            except sqlite3.OperationalError as e:
                logging.error(f"更新事件备注失败: {e}")
                self.db_conn.rollback()

    def configure_thresholds(self):
        """配置阈值"""
        dialog = ThresholdConfigDialog(self.thresholds, self.channel_names, self)
        if dialog.exec_():
            self.thresholds = dialog.get_thresholds()
            self.save_config()

    def configure_channel_names(self):
        """配置通道名称"""
        dialog = ChannelConfigDialog(self.channel_names, self)
        if dialog.exec_():
            new_names = dialog.get_channel_names()
            self.channel_names = new_names

            for col in range(9):
                item = self.data_table.item(0, col)
                item.setText(self.channel_names[col])

                item = self.data_table.item(2, col)
                item.setText(self.channel_names[col + 9])

            for i, cb in enumerate(self.channel_checkboxes):
                cb.setText(self.channel_names[i])

            self.init_plot()
            self.save_config()  # 保存新的通道名称到配置文件

    def configure_event_display(self):
        """配置事件显示方式"""
        dialog = EventDisplayModeDialog(self.event_display_mode, self)
        if dialog.exec_():
            self.event_display_mode = dialog.get_selected_mode()
            self.save_config()

    def configure_plot_mode(self):
        """配置图表显示模式"""
        dialog = PlotModeDialog(self.plot_mode, self)
        if dialog.exec_():
            self.plot_mode = dialog.get_selected_mode()
            self.save_config()
            self.init_plot()

    def show_about(self):
        """显示关于对话框"""
        QMessageBox.about(self, "关于",
                          "18路电压采集上位机优化版\n版本: 2.0\n\n支持长时间运行采集的优化版本")

    def export_data(self):
        """导出数据为Excel文档"""
        if not self.time_history:
            QMessageBox.warning(self, "警告", "没有数据可导出")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "导出数据", "", "Excel文件 (*.xlsx);;所有文件 (*)")

        if not filename:
            return

        try:
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT * FROM voltage_data")
            rows = cursor.fetchall()

            cursor.execute("PRAGMA table_info(voltage_data)")
            columns = [row[1] for row in cursor.fetchall()]

            import pandas as pd
            df = pd.DataFrame(rows, columns=columns)
            df.to_excel(filename, index=False, engine='openpyxl')

            QMessageBox.information(self, "成功", f"数据已成功导出到 {filename}")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出数据失败: {str(e)}")

    # ---------------- 配置读写 ----------------
    def load_config(self):
        if not os.path.exists(self.config_file):
            self.create_default_config()
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            self.thresholds = cfg.get('thresholds', [5.0]*18)
            self.channel_names = cfg.get('channel_names', [f"通道{i+1}" for i in range(18)])
            self.event_display_mode = cfg.get('event_display_mode', 0)
            self.plot_mode = cfg.get('plot_mode', 0)
            self.time_format = cfg.get("time_format", "seconds")  # 默认为秒
        except Exception as e:
            logging.error(f"加载配置失败: {e}")


    def save_config(self):
        try:
            cfg = {
                'thresholds': self.thresholds,
                'event_display_mode': self.event_display_mode,
                'plot_mode': self.plot_mode,
                'channel_names': self.channel_names,
                'time_format': self.time_format
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logging.error(f"保存配置失败: {e}")

    def create_default_config(self):
        default = {
            "thresholds": [5.0] * 18,
            "channel_names": [f"通道{i+1}" for i in range(18)],
            "event_display_mode": 0,
            "plot_mode": 0
        }
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(default, f, indent=4, ensure_ascii=False)
        logging.info("已创建默认配置文件 config.json")

    def shutdown(self):
        self.stop_acquisition()
        if self.modbus_client.is_connected:
            self.modbus_client.disconnect()
        if self.storage_thread:
            self.storage_thread.stop()
        if self.data_buffer:
            self._flush_remaining_data()
        if self.db_conn:
            self.db_conn.close()
        self.save_config()

    def _flush_remaining_data(self):
        cursor = self.db_conn.cursor()
        data_list = []
        while self.data_buffer:
            try:
                ts, d = self.data_buffer.popleft()
                ts_str = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')[:-3]
                data_list.append([ts_str] + d)
            except IndexError:
                break
        if data_list:
            try:
                cursor.executemany(
                    "INSERT INTO voltage_data (timestamp, channel1, channel2, channel3, channel4, channel5, channel6, channel7, channel8, channel9, channel10, channel11, channel12, channel13, channel14, channel15, channel16, channel17, channel18) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    data_list)
                self.db_conn.commit()
            except sqlite3.OperationalError as e:
                logging.error(f"最终数据存储失败: {e}")
                self.db_conn.rollback()


    def show_coordinate(self, event):
        """显示鼠标位置的坐标"""
        if self.plot_mode == 1:
            pos = event
            if self.plot_widget.sceneBoundingRect().contains(pos):
                mouse_point = self.plot_widget.getViewBox().mapSceneToView(pos)
                timestamp = mouse_point.x()

                # 检查 timestamp 是否合法
                try:
                    if timestamp < 0 or timestamp > 1e10:
                        raise ValueError("Invalid timestamp")
                    date_time_str = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')[:-3]
                    voltage = mouse_point.y()
                    self.set_status(f"时间: {date_time_str}, 电压: {voltage:.3f} V")
                except (OSError, ValueError, OverflowError):
                    self.set_status("无效时间戳")
            else:
                self.set_status("就绪")

    def zoom_in(self):
        """放大图表"""
        if self.plot_mode == 1:
            current_range = self.plot_widget.getViewBox().viewRange()
            new_range = [
                [current_range[0][0] * 1.1, current_range[0][1] * 0.9],
                [current_range[1][0] * 1.1, current_range[1][1] * 0.9]
            ]
            self.plot_widget.getViewBox().setYRange(new_range[1][0], new_range[1][1])
            self.plot_widget.getViewBox().setXRange(new_range[0][0], new_range[0][1])

    def zoom_out(self):
        """缩小图表"""
        if self.plot_mode == 1:
            current_range = self.plot_widget.getViewBox().viewRange()
            new_range = [
                [current_range[0][0] * 0.9, current_range[0][1] * 1.1],
                [current_range[1][0] * 0.9, current_range[1][1] * 1.1]
            ]
            self.plot_widget.getViewBox().setYRange(new_range[1][0], new_range[1][1])
            self.plot_widget.getViewBox().setXRange(new_range[0][0], new_range[0][1])

    def reset_view(self):
        """复位图表视图"""
        if self.plot_mode == 1 and self.time_history:
            timestamps = list(self.time_history)
            all_data = [item for sublist in self.data_history for item in sublist]
            if all_data:
                min_voltage = min(all_data) - 1
                max_voltage = max(all_data) + 1
                self.plot_widget.setYRange(min_voltage, max_voltage)
                self.plot_widget.setXRange(timestamps[0], timestamps[-1])
    def toggle_time_format(self):
        """切换事件持续时间显示格式：秒 ⇄ 0:00:00.00"""
        self.time_format = "hms" if self.time_format == "seconds" else "seconds"
        self.save_config()
        self.update_event_table()

