import sys
import os
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEngine(unittest.TestCase):
    @patch('auto_coordinator.engine.NTCJsonWriter')
    @patch('auto_coordinator.engine.DataReader')
    @patch('auto_coordinator.engine.LinkLogger')
    def setUp(self, *mocks):
        from auto_coordinator.engine import AutoCoordinator
        self.engine = AutoCoordinator()

    def test_init_state(self):
        self.assertFalse(self.engine.running)
        self.assertFalse(self.engine.paused)
        self.assertEqual(self.engine.cycle_count, 0)
        self.assertEqual(
            list(self.engine.compensation_counts.keys()),
            [1, 2, 3, 4, 5, 6]
        )

    def test_start_stop_lifecycle(self):
        self.engine.data_reader.load_mapping = MagicMock()
        self.engine.data_reader.check_alive.return_value = {
            'ntc': True, 'voltage': True
        }
        self.engine.start()
        self.assertTrue(self.engine.running)
        self.engine.ntc_writer.write_mode.assert_called_with('coordinator')

        self.engine.stop()
        self.assertFalse(self.engine.running)
        self.engine.ntc_writer.write_mode.assert_called_with('standalone')
        self.engine.ntc_writer.write_reset_all_outputs.assert_called_once()

    def test_pause_resume(self):
        self.engine.pause()
        self.assertTrue(self.engine.paused)
        self.engine.resume()
        self.assertFalse(self.engine.paused)

    def test_execute_cycle_offline_detected(self):
        self.engine.data_reader.check_alive.return_value = {
            'ntc': False, 'voltage': True
        }
        self.engine.running = True
        self.engine._execute_cycle()
        self.assertFalse(self.engine.running)


if __name__ == '__main__':
    unittest.main()
