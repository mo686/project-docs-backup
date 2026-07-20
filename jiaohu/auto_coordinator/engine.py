import json
import time
import logging
import threading
from pathlib import Path
from auto_coordinator.ntc_json_writer import NTCJsonWriter
from auto_coordinator.data_reader import DataReader
from auto_coordinator.link_logger import LinkLogger


class AutoCoordinator:
    CONTROL_INTERVAL = 0.1
    ALIVE_TIMEOUT = 10.0
    DEFAULT_RAMP_SLOPE = 5.0

    def __init__(self):
        self.running = False
        self.paused = False
        self.lock = threading.Lock()

        self._load_config()

        self.ntc_writer = NTCJsonWriter(
            Path(__file__).parent.parent / 'NTC.json'
        )

        self.data_reader = DataReader()
        self.logger = LinkLogger(self.data_reader.db_path)

        self._ramp_state = {}
        self._recover_cycles = {}
        self.cycle_count = 0

        self.on_state_changed = None

    def _load_config(self):
        config_path = Path(__file__).parent / 'config.json'
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        self.CONTROL_INTERVAL = cfg.get('control_interval_ms', 100) / 1000.0
        self.ALIVE_TIMEOUT = cfg.get('alive_timeout_s', 10.0)
        self.DEFAULT_RAMP_SLOPE = cfg.get('default_ramp_slope', 5.0)
        self.DEFAULT_COOL_SLOPE = cfg.get('default_cool_slope', 5.0)
        self.RECOVER_CYCLES = cfg.get('recover_cycles', 5)
        self.STATE_RETENTION_DAYS = cfg.get('state_retention_days', 7)
        self._ramp_directions = cfg.get('ramp_directions', {})
        self._channel_ramp_slopes = cfg.get('channel_ramp_slopes', {})
        log_level = cfg.get('log_level', 'INFO')
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format='%(asctime)s [%(levelname)s] %(message)s'
        )

    def start(self):
        self.data_reader.load_mapping()
        self.ntc_writer.write_mode('coordinator')
        self.running = True
        self.cycle_count = 0
        self._ramp_state = {}
        self._recover_cycles = {}
        thread = threading.Thread(target=self._control_loop, daemon=True)
        thread.start()

    def stop(self):
        self.running = False
        self.ntc_writer.write_mode('standalone')
        self.ntc_writer.write_reset_all_outputs()
        self.logger.flush()

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
                    logging.error(f"error: {e}", exc_info=True)

            time.sleep(self.CONTROL_INTERVAL)

    def _execute_cycle(self):
        self.cycle_count += 1

        alive = self.data_reader.check_alive(self.ALIVE_TIMEOUT)
        if not alive['ntc'] or not alive['voltage']:
            self.logger.log('AUTO_STOP', None, 'offline',
                            {'ntc': alive['ntc'], 'voltage': alive['voltage']})
            self.running = False
            return

        voltages = self.data_reader.read_voltage_data()
        ntc_state = self.data_reader.read_ntc_state()
        thresholds = self.data_reader.read_thresholds()
        if voltages is None or ntc_state is None:
            return

        state_snapshot = {}

        for ntc_ch, v_ch in self.data_reader.mapping.items():
            ch_idx = ntc_ch - 1

            actual_v = voltages[v_ch - 1]
            threshold = thresholds[v_ch - 1]
            slope_config = self._get_channel_ramp_slope(ntc_ch)
            direction = self._get_ramp_direction(ntc_ch)

            is_ramping = self._ramp_state.get(ntc_ch, {}).get('active', False)

            if actual_v > threshold:
                target_slope = slope_config
                if direction == 'decrease':
                    target_slope = -target_slope

                self.ntc_writer.write_cmd(ntc_ch, slope=target_slope)
                self._ramp_state[ntc_ch] = {
                    'active': True,
                    'slope': target_slope,
                }
                self._recover_cycles.pop(ntc_ch, None)
                self.logger.log('THRESHOLD_TRIGGER', ntc_ch,
                                f'{actual_v:.3f}V > {threshold:.3f}V',
                                {'actual_v': actual_v, 'threshold': threshold,
                                 'slope': target_slope})
            elif is_ramping and actual_v <= threshold:
                cycles = self._recover_cycles.get(ntc_ch, 0) + 1
                if cycles >= self.RECOVER_CYCLES:
                    self.ntc_writer.write_cmd(ntc_ch, slope=0.0)
                    self._ramp_state.pop(ntc_ch, None)
                    self._recover_cycles.pop(ntc_ch, None)
                    self.logger.log('THRESHOLD_RECOVER', ntc_ch,
                                    f'{actual_v:.3f}V <= {threshold:.3f}V',
                                    {'actual_v': actual_v, 'threshold': threshold})
                else:
                    self._recover_cycles[ntc_ch] = cycles

            state_snapshot[ntc_ch] = {
                'target_temp': ntc_state['temps'][ch_idx],
                'actual_voltage': actual_v,
                'threshold': threshold,
                'slope': ntc_state['slopes'][ch_idx],
                'auto_run': ntc_state['auto_run'][ch_idx],
                'is_ramping': self._ramp_state.get(ntc_ch, {}).get('active', False),
                'is_over': actual_v > threshold,
                'channel_name': ntc_state.get('ch_names', [f'CH{i}' for i in range(1, 7)])[ch_idx],
            }

        self.logger.write_state_snapshot(state_snapshot)

        if self.cycle_count % 50 == 0:
            self.logger.flush()

        if self.on_state_changed:
            self.on_state_changed(state_snapshot)

    def _get_ramp_direction(self, ntc_ch: int) -> str:
        key = str(ntc_ch)
        raw = self._ramp_directions.get(key, 'decrease')
        if raw in ('decrease', 'increase'):
            return raw
        if raw in (1, '1', True, 'true'):
            return 'increase'
        return 'decrease'

    def _get_channel_ramp_slope(self, ntc_ch: int) -> float:
        key = str(ntc_ch)
        return float(self._channel_ramp_slopes.get(key, self.DEFAULT_RAMP_SLOPE))
