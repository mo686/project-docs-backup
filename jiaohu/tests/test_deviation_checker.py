import sys
import os
import unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_coordinator.deviation_checker import DeviationChecker


class TestDeviationChecker(unittest.TestCase):
    def setUp(self):
        self.checker = DeviationChecker()

    def test_normal_within_tolerance(self):
        result = self.checker.check(0.02, 0.05)
        self.assertEqual(result, 'normal')

    def test_normal_exact_tolerance(self):
        result = self.checker.check(0.05, 0.05)
        self.assertEqual(result, 'normal')

    def test_normal_negative_within_tolerance(self):
        result = self.checker.check(-0.03, 0.05)
        self.assertEqual(result, 'normal')

    def test_compensate_above_tolerance(self):
        result = self.checker.check(0.10, 0.05)
        self.assertEqual(result, 'compensate')

    def test_compensate_below_hw_fault(self):
        result = self.checker.check(0.49, 0.02)
        self.assertEqual(result, 'compensate')

    def test_hw_fault_above_threshold(self):
        result = self.checker.check(0.60, 0.05)
        self.assertEqual(result, 'hw_fault')

    def test_hw_fault_negative_above_threshold(self):
        result = self.checker.check(-0.55, 0.05)
        self.assertEqual(result, 'hw_fault')


if __name__ == '__main__':
    unittest.main()
