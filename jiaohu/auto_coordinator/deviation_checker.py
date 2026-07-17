class DeviationChecker:
    """偏差分级判定器"""

    HW_FAULT_THRESHOLD = 0.5

    @staticmethod
    def check(deviation: float, tolerance: float) -> str:
        abs_dev = abs(deviation)
        if abs_dev <= tolerance:
            return 'normal'
        if abs_dev > DeviationChecker.HW_FAULT_THRESHOLD:
            return 'hw_fault'
        return 'compensate'
