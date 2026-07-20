import json
import time
import datetime
import sqlite3
from pathlib import Path
from typing import Optional, List


class NTCConverter:
    """复用 NTC.py 的 NTCConverter 逻辑，扩展逆向查表"""

    def __init__(self, k_table: dict, pullup_resistor_kohm: float = 10.0):
        if not k_table:
            raise ValueError("NTC曲线数据表不能为空")
        self.table = k_table
        self.temps = sorted(self.table.keys())
        self.pullup_resistor_kohm = pullup_resistor_kohm
        self.voltage_cache = [self._calc_voltage(t) for t in range(-50, 151)]
        self._build_inverse_cache()

    def _calc_voltage(self, t: int) -> float:
        if t <= self.temps[0]:
            r_k = self.table[self.temps[0]]
        elif t >= self.temps[-1]:
            r_k = self.table[self.temps[-1]]
        else:
            r_k = self.table[self.temps[-1]]
            for i in range(len(self.temps) - 1):
                t1, t2 = self.temps[i], self.temps[i + 1]
                if t1 <= t <= t2:
                    r_k = self.table[t1] + (self.table[t2] - self.table[t1]) * (t - t1) / (t2 - t1)
                    break
        r = max(0.001, r_k * 1000)
        pullup_r = self.pullup_resistor_kohm * 1000
        return max(0.0, min(5.0, 5.0 * r / (r + pullup_r)))

    def temp2voltage(self, t: float) -> float:
        t_int = int(round(t))
        idx = max(-50, min(150, t_int)) + 50
        return self.voltage_cache[idx]

    def _build_inverse_cache(self):
        self.inverse_cache = []
        for mv in range(0, 5001, 10):
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
        mv = max(0, min(5000, int(round(v * 1000))))
        idx = mv // 10
        return float(self.inverse_cache[min(idx, 500)])


class DataReader:
    """统一数据读取层"""

    def __init__(self):
        self.BASE_DIR = Path(__file__).parent
        self.db_path = str(self.BASE_DIR / 'voltage_data.db')
        self.config_path = str(self.BASE_DIR / 'config.json')
        self.ntc_json_path = self.BASE_DIR / 'NTC.json'
        self.ntc_xlsx_path = self.BASE_DIR / 'NTC.xlsx'

        self.mapping = {}

        self.curves = {}
        self._load_curves()

    def _load_curves(self):
        try:
            from NTC import load_ntc_curves_from_excel
            self.curves = load_ntc_curves_from_excel(self.ntc_xlsx_path)
        except Exception:
            self.curves = {}
        if not self.curves:
            raise RuntimeError("无法加载NTC曲线数据")

    def load_mapping(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ntc_channel, voltage_channel FROM channel_mapping"
        )
        for row in cursor.fetchall():
            ntc_ch, v_ch = row
            self.mapping[ntc_ch] = v_ch
        conn.close()

    def read_voltage_data(self) -> Optional[List[float]]:
        try:
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
                return list(row[:-1])
            return None
        except Exception:
            return None

    def read_ntc_state(self) -> Optional[dict]:
        raw1 = self._read_json_safe(self.ntc_json_path)
        raw2 = self._read_json_safe(self.ntc_json_path)
        if raw1 != raw2:
            raw3 = self._read_json_safe(self.ntc_json_path)
            if raw2 != raw3:
                return None
        cfg = raw2 or {}
        return {
            'temps': cfg.get('ntc_temps', [25] * 6),
            'curves': cfg.get('ntc_curves', ['10k'] * 6),
            'slopes': cfg.get('slope', [5.0] * 6),
            'auto_run': cfg.get('auto_run', [False] * 6),
            'limit_high': cfg.get('limit_temp_high', [100] * 6),
            'limit_low': cfg.get('limit_temp_low', [-50] * 6),
            'pullup_resistors': cfg.get('pullup_resistors', [10.0] * 6),
            'ch_names': cfg.get('ch_names', [f'CH{i}' for i in range(1, 7)]),
            'heartbeat': cfg.get('_heartbeat', {}),
        }

    def read_thresholds(self) -> List[float]:
        cfg = self._read_json_safe(self.config_path)
        if cfg:
            return cfg.get('thresholds', [5.0] * 18)
        return [5.0] * 18

    def check_alive(self, timeout: float) -> dict:
        result = {'ntc': False, 'voltage': False}
        try:
            cfg = self._read_json_safe(self.ntc_json_path)
            if cfg:
                hb = cfg.get('_heartbeat', {})
                ts = hb.get('timestamp', 0)
                if time.time() - ts < timeout:
                    result['ntc'] = True
        except Exception:
            pass
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
        except Exception:
            pass
        return result

    @staticmethod
    def _read_json_safe(path) -> Optional[dict]:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
