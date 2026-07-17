from auto_coordinator.ntc_json_writer import NTCJsonWriter


class RampDriver:
    """自动斜坡驱动器 (联动引擎侧)"""

    MIN_TEMP = -50.0
    MAX_TEMP = 150.0

    def __init__(self, data_reader, ntc_writer: NTCJsonWriter):
        self.data_reader = data_reader
        self.ntc_writer = ntc_writer

    def step(self, ntc_ch: int, ntc_state: dict, ch_idx: int) -> bool:
        slope = ntc_state['slopes'][ch_idx]
        current_temp = ntc_state['temps'][ch_idx]
        limit_high = ntc_state['limit_high'][ch_idx]
        limit_low = ntc_state['limit_low'][ch_idx]

        delta = (slope / 60.0) * 0.1
        new_temp = current_temp + delta
        limited = False

        if slope >= 0 and new_temp >= limit_high:
            new_temp = float(limit_high)
            limited = True
        elif slope < 0 and new_temp <= limit_low:
            new_temp = float(limit_low)
            limited = True

        new_temp = max(self.MIN_TEMP, min(self.MAX_TEMP, new_temp))
        self.ntc_writer.write_cmd(ntc_ch, temp=new_temp)
        return limited
