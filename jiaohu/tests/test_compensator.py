import sys
import os
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_coordinator.compensator import Compensator


class TestCompensator(unittest.TestCase):
    def setUp(self):
        self.data_reader = MagicMock()
        self.ntc_writer = MagicMock()
        self.compensator = Compensator(self.data_reader, self.ntc_writer)

    def test_calc_basic_compensation(self):
        comp = self.compensator.calc(1, 0.10, {})
        self.assertIsNotNone(comp)
        self.assertAlmostEqual(comp, -0.05, delta=0.01)

    def test_calc_negative_deviation(self):
        comp = self.compensator.calc(1, -0.10, {})
        self.assertIsNotNone(comp)
        self.assertAlmostEqual(comp, 0.05, delta=0.01)

    def test_calc_clamped_to_max(self):
        comp = self.compensator.calc(1, 1.00, {})
        self.assertIsNotNone(comp)
        self.assertEqual(comp, -0.20)

    def test_calc_clamped_to_negative_max(self):
        comp = self.compensator.calc(1, -1.00, {})
        self.assertIsNotNone(comp)
        self.assertEqual(comp, 0.20)

    def test_calc_retry_limit_reached(self):
        counts = {1: 10}
        comp = self.compensator.calc(1, 0.10, counts)
        self.assertIsNone(comp)

    def test_is_compensation_limit(self):
        counts = {1: 10}
        self.assertTrue(self.compensator.is_compensation_limit(1, counts))

    def test_is_compensation_limit_false(self):
        counts = {1: 5}
        self.assertFalse(self.compensator.is_compensation_limit(1, counts))

    def test_apply_calls_writer(self):
        self.data_reader.read_ntc_state.return_value = {
            'temps': [30.0, 25.0, 25.0, 25.0, 25.0, 25.0],
            'curves': ['10k'] * 6,
            'pullup_resistors': [10.0] * 6,
        }
        self.data_reader.curves = {'10k': {25: 10.0, 30: 8.0, 100: 1.0}}
        self.compensator.apply(1, -0.05)
        self.ntc_writer.write_cmd.assert_called_once()


if __name__ == '__main__':
    unittest.main()
