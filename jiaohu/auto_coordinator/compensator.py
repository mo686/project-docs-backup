from typing import Optional
from auto_coordinator.data_reader import NTCConverter
from auto_coordinator.ntc_json_writer import NTCJsonWriter


class Compensator:
    """自动补偿器"""

    DAMPING = 0.5
    MAX_PER_CYCLE = 0.2
    MAX_RETRY = 10

    def __init__(self, data_reader, ntc_writer: NTCJsonWriter):
        self.data_reader = data_reader
        self.ntc_writer = ntc_writer

    def calc(self, ntc_ch: int, deviation: float,
             compensation_counts: dict) -> Optional[float]:
        if compensation_counts.get(ntc_ch, 0) >= self.MAX_RETRY:
            return None
        comp_v = -deviation * self.DAMPING
        comp_v = max(-self.MAX_PER_CYCLE, min(self.MAX_PER_CYCLE, comp_v))
        return comp_v

    def apply(self, ntc_ch: int, compensation_v: float):
        state = self.data_reader.read_ntc_state()
        if not state:
            return
        ch_idx = ntc_ch - 1
        current_temp = state['temps'][ch_idx]
        current_curve = state['curves'][ch_idx]
        current_pullup = state['pullup_resistors'][ch_idx]
        conv = NTCConverter(
            self.data_reader.curves[current_curve],
            current_pullup
        )
        expected_v = conv.temp2voltage(current_temp)
        target_v = expected_v + compensation_v
        target_v = max(0.0, min(5.0, target_v))
        target_temp = conv.voltage2temp_approx(target_v)
        target_temp = max(-50.0, min(150.0, target_temp))
        self.ntc_writer.write_cmd(ntc_ch, temp=target_temp)

    def is_compensation_limit(self, ntc_ch: int,
                              compensation_counts: dict) -> bool:
        return compensation_counts.get(ntc_ch, 0) >= self.MAX_RETRY
