import sys
import os
import unittest
from unittest.mock import MagicMock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_coordinator.slope_guard import SlopeGuard


class TestSlopeGuard(unittest.TestCase):
    def setUp(self):
        self.data_reader = MagicMock()
        self.ntc_writer = MagicMock()
        self.guard = SlopeGuard(self.data_reader, self.ntc_writer)

    def test_reduce_creates_state(self):
        self.guard.reduce(1, 5.0)
        self.assertIn(1, self.guard.state)
        st = self.guard.state[1]
        self.assertEqual(st['original_slope'], 5.0)
        self.assertAlmostEqual(st['current_slope'], 2.5)
        self.assertEqual(st['reduce_level'], 1)
        self.ntc_writer.write_cmd.assert_called_with(1, slope=2.5)

    def test_reduce_custom_factor(self):
        self.guard.reduce(1, 5.0, factor=0.3)
        self.assertAlmostEqual(self.guard.state[1]['current_slope'], 1.5)

    def test_reduce_min_slope(self):
        self.guard.reduce(1, 0.2, factor=0.5)
        self.assertEqual(self.guard.state[1]['current_slope'], 0.1)

    def test_reduce_max_levels_reached(self):
        self.guard.state[1] = {
            'original_slope': 5.0, 'current_slope': 0.1,
            'cycles_recovering': 0, 'reduce_level': 2,
            'last_v': 0.0, 'rising_count': 0,
        }
        self.guard.reduce(1, 0.1)
        self.guard.reduce(1, 0.1)
        self.assertEqual(self.guard.state[1]['reduce_level'], 2)

    def test_check_re_reduce_no_state(self):
        result = self.guard.check_re_reduce(1, 3.0)
        self.assertFalse(result)

    def test_check_re_reduce_rising_count(self):
        self.guard.state[1] = {
            'original_slope': 5.0, 'current_slope': 2.5,
            'cycles_recovering': 0, 'reduce_level': 1,
            'last_v': 2.0, 'rising_count': 0,
        }
        self.assertFalse(self.guard.check_re_reduce(1, 2.1))
        self.assertFalse(self.guard.check_re_reduce(1, 2.2))
        result = self.guard.check_re_reduce(1, 2.3)
        self.assertTrue(result)
        self.assertEqual(self.guard.state[1]['reduce_level'], 2)

    def test_check_re_reduce_resets_on_drop(self):
        self.guard.state[1] = {
            'original_slope': 5.0, 'current_slope': 2.5,
            'cycles_recovering': 0, 'reduce_level': 1,
            'last_v': 3.0, 'rising_count': 2,
        }
        self.assertFalse(self.guard.check_re_reduce(1, 2.9))
        self.assertEqual(self.guard.state[1]['rising_count'], 0)

    def test_try_restore_after_cycles(self):
        self.guard.state[1] = {
            'original_slope': 5.0, 'current_slope': 2.5,
            'cycles_recovering': 4, 'reduce_level': 1,
            'last_v': 0.0, 'rising_count': 0,
        }
        self.guard.try_restore(1, 3.0, 5.0)
        self.assertNotIn(1, self.guard.state)
        self.ntc_writer.write_cmd.assert_called_with(1, slope=5.0)

    def test_try_restore_resets_on_over_threshold(self):
        self.guard.state[1] = {
            'original_slope': 5.0, 'current_slope': 2.5,
            'cycles_recovering': 3, 'reduce_level': 1,
            'last_v': 0.0, 'rising_count': 0,
        }
        self.guard.try_restore(1, 5.1, 5.0)
        self.assertEqual(self.guard.state[1]['cycles_recovering'], 0)


if __name__ == '__main__':
    unittest.main()
