#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
6 通道 NTC 后端模块（纯逻辑，UI已迁移至 auto_coordinator/widgets/ntc_sim_tab.py）
提供：load_ntc_curves_from_excel, NTCConverter, HKModule
"""
import json
import os
import sys
import time
from pathlib import Path
import pandas as pd
from PyQt5.QtCore import QObject, QThread, QMutex, QMutexLocker, pyqtSignal

# ---------- 配置文件路径 ----------
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

CFG_EXCEL_FILE = BASE_DIR / (Path(__file__).stem + '.xlsx')
CFG_JSON_FILE = BASE_DIR / (Path(__file__).stem + '.json')

REG_MAX = 5000


def load_ntc_curves_from_excel(file_path):
    """加载NTC曲线数据，返回字典 {sheet_name: {temp: resistance}}"""
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

        self.voltage_cache = [self._calc_voltage(t) for t in range(-50, 151)]

    def _calc_voltage(self, t):
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

    def temp2voltage(self, t):
        """运行时直接查缓存，复杂度O(1)"""
        t_int = int(round(t))
        idx = max(-50, min(150, t_int)) + 50
        return self.voltage_cache[idx]


class HKModule(QObject):
    """串口写入模块（使用队列和独立线程，避免阻塞）"""
    writeError = pyqtSignal(int, str)

    def __init__(self, port, addr=1, baud=9600):
        super().__init__()
        self.port = port
        self.addr = addr
        self.baud = baud
        self._instrument = None
        self._write_queue = []
        self._queue_lock = QMutex()
        self._worker_thread = None
        self._stop_flag = False

    def _get_instrument(self):
        if self._instrument is None:
            import minimalmodbus as modbus
            try:
                self._instrument = modbus.Instrument(self.port, self.addr)
                self._instrument.serial.baudrate = self.baud
                self._instrument.serial.timeout = 0.2
                self._instrument.close_port_after_each_call = True
            except Exception as e:
                raise ConnectionError(f"无法初始化串口仪器: {e}")
        return self._instrument

    def write_voltage(self, ch, v):
        val = int(round(v * 1000))
        val = max(0, min(REG_MAX, val))
        with QMutexLocker(self._queue_lock):
            self._write_queue = [(ch, val)]
            if self._worker_thread is None or not self._worker_thread.isRunning():
                self._start_worker()

    def _start_worker(self):
        self._stop_flag = False
        self._worker_thread = QThread()
        self._worker_thread.run = self._process_queue
        self._worker_thread.start()

    def _process_queue(self):
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
            QThread.msleep(10)

    def close(self):
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


# ---------- 独立启动入口（已废弃，UI迁移至 auto_coordinator） ----------
if __name__ == "__main__":
    print("NTC.py 后端模块。UI 已迁移至 auto_coordinator/widgets/ntc_sim_tab.py")
    print("请使用 python -m auto_coordinator.app 启动统一界面。")
