import json
import logging
import sqlite3
import datetime
from collections import deque
from typing import Optional


class LinkLogger:

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._state_queue = deque(maxlen=50)

    def log(self, event_type: str, ntc_channel: Optional[int],
            description: str, detail: dict = None):
        ch_label = f"CH{ntc_channel}" if ntc_channel else "-"
        print(f"[{event_type}] {ch_label}  {description}")

    def write_state_snapshot(self, state: dict):
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        for ntc_ch, data in state.items():
            event_type = 'trigger' if data.get('is_over') else 'normal'
            self._state_queue.append({
                'timestamp': ts,
                'ntc_channel': ntc_ch,
                'target_temp': data['target_temp'],
                'actual_voltage': data['actual_voltage'],
                'slope_current': data['slope'],
                'is_triggered': 1 if data.get('is_ramping') else 0,
                'event_type': event_type,
            })

    def flush(self):
        if not self._state_queue:
            return
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            while self._state_queue:
                st = self._state_queue.popleft()
                cursor.execute(
                    "INSERT INTO link_run_state (timestamp, ntc_channel, "
                    "target_temp, actual_voltage, slope_current, is_triggered, "
                    "event_type) VALUES (?,?,?,?,?,?,?)",
                    (st['timestamp'], st['ntc_channel'], st['target_temp'],
                     st['actual_voltage'], st['slope_current'], st['is_triggered'],
                     st['event_type'])
                )

            conn.commit()
        except Exception as e:
            logging.error(f"LinkLogger flush error: {e}")
        finally:
            conn.close()
