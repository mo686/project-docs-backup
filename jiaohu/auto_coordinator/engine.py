import json
import time
import logging
import threading
from pathlib import Path
from auto_coordinator.ntc_json_writer import NTCJsonWriter
from auto_coordinator.data_reader import DataReader
from auto_coordinator.deviation_checker import DeviationChecker
from auto_coordinator.compensator import Compensator
from auto_coordinator.slope_guard import SlopeGuard

from auto_coordinator.ramp_driver import RampDriver
from auto_coordinator.link_logger import LinkLogger


class AutoCoordinator:
    """联动调度引擎"""

    CONTROL_INTERVAL = 0.1
    MAX_DEVIATION_HW = 0.5
    COMPENSATION_DAMPING = 0.5
    COMPENSATION_MAX = 0.2
    COMPENSATION_RETRY_MAX = 10
    SLOPE_REDUCE_DEFAULT = 0.5
    SLOPE_RESTORE_CYCLES = 5
    ALIVE_TIMEOUT = 10.0

    def __init__(self):
        self.running = False
        self.paused = False
        self.lock = threading.Lock()

        self._load_config()

        self.ntc_writer = NTCJsonWriter(
            Path(__file__).parent.parent / 'NTC.json'
        )

        self.data_reader = DataReader()
        self.deviation_checker = DeviationChecker()
        self.compensator = Compensator(self.data_reader, self.ntc_writer)
        self.slope_guard = SlopeGuard(self.data_reader, self.ntc_writer)
        self.ramp_driver = RampDriver(self.data_reader, self.ntc_writer)
        self.logger = LinkLogger(self.data_reader.db_path)

        self.compensation_counts = {i: 0 for i in range(1, 7)}
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
        self.MAX_DEVIATION_HW = cfg.get('max_deviation_hw_v', 0.5)
        self.COMPENSATION_DAMPING = cfg.get('compensation_damping', 0.5)
        self.COMPENSATION_MAX = cfg.get('compensation_max_per_cycle_v', 0.2)
        self.COMPENSATION_RETRY_MAX = cfg.get('compensation_retry_max', 10)
        self.SLOPE_REDUCE_DEFAULT = cfg.get('slope_reduce_default', 0.5)
        self.SLOPE_RESTORE_CYCLES = cfg.get('slope_restore_cycles', 5)
        self.ALIVE_TIMEOUT = cfg.get('alive_timeout_s', 10.0)
        self.STATE_RETENTION_DAYS = cfg.get('state_retention_days', 7)
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
        self.compensation_counts = {i: 0 for i in range(1, 7)}
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
                    logging.error(f"控制循环异常: {e}", exc_info=True)

            time.sleep(self.CONTROL_INTERVAL)

    def _execute_cycle(self):
        self.cycle_count += 1

        alive = self.data_reader.check_alive(self.ALIVE_TIMEOUT)
        if not alive['ntc'] or not alive['voltage']:
            self.logger.log('AUTO_STOP', None, '上位机离线',
                            {'ntc': alive['ntc'], 'voltage': alive['voltage']})
            self.running = False
            return

        voltages = self.data_reader.read_voltage_data()
        ntc_state = self.data_reader.read_ntc_state()
        thresholds = self.data_reader.read_thresholds()
        if voltages is None or ntc_state is None:
            return

        state_snapshot = {}
        all_channels_done = True

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

            if actual_v > thresholds[v_ch - 1]:
                self.slope_guard.reduce(ntc_ch, ntc_state['slopes'][ch_idx],
                                        self.SLOPE_REDUCE_DEFAULT)
                limit_flag = True
                self.logger.log('SLOPE_ADJ', ntc_ch,
                                f'超阈值: {actual_v:.3f}V > {thresholds[v_ch-1]:.3f}V')
            else:
                self.slope_guard.try_restore(ntc_ch, actual_v, thresholds[v_ch - 1])
                if self.slope_guard.check_re_reduce(ntc_ch, actual_v):
                    self.logger.log('SLOPE_ADJ_ESCALATE', ntc_ch,
                                    '降斜率后仍持续上升，再次降斜率')

            if ntc_state['auto_run'][ch_idx]:
                step_limited = self.ramp_driver.step(ntc_ch, ntc_state, ch_idx)
                limit_flag = limit_flag or step_limited
            else:
                all_channels_done = False

            if not limit_flag:
                all_channels_done = False

            if abs(deviation) <= tol:
                self.compensation_counts[ntc_ch] = 0

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
                'channel_name': ntc_state.get('ch_names', [f'CH{i}' for i in range(1, 7)])[ch_idx],
                'status': status,
            }

        self.logger.write_state_snapshot(state_snapshot)

        if self.cycle_count % 50 == 0:
            self.logger.flush()

        if self.on_state_changed:
            self.on_state_changed(state_snapshot)
