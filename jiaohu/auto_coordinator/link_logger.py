import json
import logging
import sqlite3
import datetime
from collections import deque
from typing import Optional


class LinkLogger:
    """联动日志记录器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._state_queue = deque(maxlen=50)
        self._event_queue = deque(maxlen=100)

    def log(self, event_type: str, ntc_channel: Optional[int],
            description: str, detail: dict = None):
        self._event_queue.append({
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            'event_type': event_type,
            'ntc_channel': ntc_channel,
            'description': description,
            'detail': json.dumps(detail or {}, ensure_ascii=False),
        })

    def write_state_snapshot(self, state: dict):
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
        if not self._event_queue and not self._state_queue:
            return
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            while self._event_queue:
                evt = self._event_queue.popleft()
                cursor.execute(
                    "INSERT INTO link_events (timestamp, event_type, ntc_channel, "
                    "description, detail) VALUES (?,?,?,?,?)",
                    (evt['timestamp'], evt['event_type'], evt['ntc_channel'],
                     evt['description'], evt['detail'])
                )

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
        except Exception as e:
            logging.error(f"LinkLogger flush 异常: {e}")
        finally:
            conn.close()
