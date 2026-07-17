import json
import threading
from pathlib import Path


class NTCJsonWriter:
    """NTC.json 原子写入器，串联所有写 NTC.json 的模块"""

    def __init__(self, ntc_json_path: Path):
        self.path = ntc_json_path
        self.tmp_path = ntc_json_path.with_suffix('.tmp')
        self._lock = threading.Lock()

    def write_cmd(self, ntc_ch: int, **kwargs):
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
        with self._lock:
            cfg = self._read_cfg()
            cfg['_mode'] = mode
            self._write_cfg(cfg)

    def write_reset_all_outputs(self):
        with self._lock:
            cfg = self._read_cfg()
            cfg['_external_command'] = {}
            for ch_idx in range(6):
                cfg['_external_command'][str(ch_idx)] = {
                    'temp': 25.0, 'slope': 0.0, 'auto_run': False
                }
            self._write_cfg(cfg)

    def clear_commands(self):
        with self._lock:
            cfg = self._read_cfg()
            cfg['_external_command'] = {}
            self._write_cfg(cfg)

    def _read_cfg(self) -> dict:
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _write_cfg(self, cfg: dict):
        with open(self.tmp_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        self.tmp_path.replace(self.path)
