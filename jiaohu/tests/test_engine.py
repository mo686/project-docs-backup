import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_alive(ntc=True, voltage=True):
    return {'ntc': ntc, 'voltage': voltage}


def _make_ntc_state():
    return {
        'temps': [25.0] * 6,
        'curves': ['10k'] * 6,
        'pullup_resistors': [10.0] * 6,
        'slopes': [5.0] * 6,
        'auto_run': [False] * 6,
        'ch_names': [f'CH{i}' for i in range(1, 7)],
    }


def _make_voltages():
    return [3.0] * 18


def _make_thresholds():
    return [4.5] * 18


class TestEngine(unittest.TestCase):
    @patch('auto_coordinator.engine.NTCJsonWriter')
    @patch('auto_coordinator.engine.DataReader')
    @patch('auto_coordinator.engine.LinkLogger')
    def setUp(self, mock_logger, mock_reader, mock_writer):
        from auto_coordinator.engine import AutoCoordinator
        self.engine = AutoCoordinator()
        self.mock_reader = self.engine.data_reader
        self.mock_writer = self.engine.ntc_writer
        self.mock_logger = self.engine.logger

    def test_init_state(self):
        self.assertFalse(self.engine.running)
        self.assertFalse(self.engine.paused)

    def test_start_lifecycle(self):
        self.mock_reader.load_mapping = MagicMock()
        self.mock_reader.check_alive.return_value = _make_alive()
        self.mock_reader.read_voltage_data.return_value = _make_voltages()
        self.mock_reader.read_ntc_state.return_value = _make_ntc_state()
        self.mock_reader.read_thresholds.return_value = _make_thresholds()

        self.engine.start()
        self.assertTrue(self.engine.running)
        self.mock_reader.load_mapping.assert_called_once()
        self.mock_writer.write_mode.assert_called_with('coordinator')

        self.engine.stop()
        self.assertFalse(self.engine.running)
        self.mock_writer.write_mode.assert_called_with('standalone')
        self.mock_writer.write_reset_all_outputs.assert_called_once()
        self.mock_logger.flush.assert_called_once()

    def test_pause_resume(self):
        self.engine.pause()
        self.assertTrue(self.engine.paused)
        self.engine.resume()
        self.assertFalse(self.engine.paused)

    def test_cycle_voltage_under_threshold_no_ramp(self):
        self.engine.running = True
        self.mock_reader.check_alive.return_value = _make_alive()
        voltages = [3.0] * 18
        thresholds = [4.5] * 18
        self.mock_reader.read_voltage_data.return_value = voltages
        self.mock_reader.read_ntc_state.return_value = _make_ntc_state()
        self.mock_reader.read_thresholds.return_value = thresholds

        self.engine._execute_cycle()

        self.assertTrue(self.engine.running)
        self.assertFalse(self.mock_writer.write_cmd.called)
        trigger_calls = [
            call for call in self.mock_logger.log.call_args_list
            if 'THRESHOLD_TRIGGER' in str(call[0])
        ]
        self.assertEqual(len(trigger_calls), 0)

    def test_cycle_voltage_over_threshold_triggers_ramp(self):
        self.engine.running = True
        self.mock_reader.check_alive.return_value = _make_alive()
        voltages = [5.0] * 18
        thresholds = [4.5] * 18
        self.mock_reader.read_voltage_data.return_value = voltages
        self.mock_reader.read_ntc_state.return_value = _make_ntc_state()
        self.mock_reader.read_thresholds.return_value = thresholds
        self.mock_reader.mapping = {1: 1}

        self.engine._execute_cycle()

        self.mock_writer.write_cmd.assert_called()
        trigger_calls = [
            call for call in self.mock_logger.log.call_args_list
            if call[0][0] == 'THRESHOLD_TRIGGER'
        ]
        self.assertGreaterEqual(len(trigger_calls), 1)

    def test_cycle_voltage_recovers_stops_ramp(self):
        self.engine.running = True
        self.mock_reader.check_alive.return_value = _make_alive()
        self.mock_reader.mapping = {1: 1}

        self.mock_reader.read_voltage_data.return_value = [5.0] * 18
        self.mock_reader.read_ntc_state.return_value = _make_ntc_state()
        self.mock_reader.read_thresholds.return_value = [4.5] * 18
        self.engine._execute_cycle()

        self.mock_reader.read_voltage_data.return_value = [3.0] * 18
        for _ in range(self.engine.RECOVER_CYCLES):
            self.engine._execute_cycle()

        recover_calls = [
            call for call in self.mock_logger.log.call_args_list
            if call[0][0] == 'THRESHOLD_RECOVER'
        ]
        self.assertGreaterEqual(len(recover_calls), 1)

    def test_cycle_same_channel_continues_ramp(self):
        self.engine.running = True
        self.mock_reader.check_alive.return_value = _make_alive()
        self.mock_reader.mapping = {1: 1}
        self.mock_reader.read_ntc_state.return_value = _make_ntc_state()

        voltages_over = [5.0] * 18
        thresholds = [4.5] * 18

        self.mock_reader.read_voltage_data.return_value = voltages_over
        self.mock_reader.read_thresholds.return_value = thresholds
        self.engine._execute_cycle()

        self.mock_reader.read_voltage_data.return_value = voltages_over
        self.mock_reader.read_thresholds.return_value = thresholds
        self.engine._execute_cycle()

        trigger_calls = [
            call for call in self.mock_logger.log.call_args_list
            if call[0][0] == 'THRESHOLD_TRIGGER'
        ]
        self.assertEqual(len(trigger_calls), 2)
        self.assertTrue(self.engine._ramp_state[1]['active'])

    def test_cycle_offline_detection_auto_stop(self):
        self.engine.running = True
        self.mock_reader.check_alive.return_value = _make_alive(ntc=False)

        self.engine._execute_cycle()

        self.assertFalse(self.engine.running)
        stop_calls = [
            call for call in self.mock_logger.log.call_args_list
            if call[0][0] == 'AUTO_STOP'
        ]
        self.assertEqual(len(stop_calls), 1)

    def test_cycle_null_data_skips(self):
        self.engine.running = True
        self.mock_reader.check_alive.return_value = _make_alive()
        self.mock_reader.read_voltage_data.return_value = None
        self.mock_reader.read_ntc_state.return_value = _make_ntc_state()

        self.engine._execute_cycle()

        self.assertTrue(self.engine.running)
        self.assertFalse(self.mock_writer.write_cmd.called)


if __name__ == '__main__':
    unittest.main()
