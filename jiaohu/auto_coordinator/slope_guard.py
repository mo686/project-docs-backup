from auto_coordinator.ntc_json_writer import NTCJsonWriter


class SlopeGuard:
    """斜率卫士"""

    REDUCE_FACTOR = 0.5
    RESTORE_CYCLES = 5
    MIN_SLOPE = 0.1
    RE_REDUCE_THRESHOLD = 3
    MAX_REDUCE_LEVELS = 2

    def __init__(self, data_reader, ntc_writer: NTCJsonWriter):
        self.data_reader = data_reader
        self.ntc_writer = ntc_writer
        self.state = {}

    def reduce(self, ntc_ch: int, current_slope: float, factor: float = None):
        if factor is None:
            factor = self.REDUCE_FACTOR
        if ntc_ch not in self.state:
            self.state[ntc_ch] = {
                'original_slope': current_slope,
                'current_slope': current_slope,
                'cycles_recovering': 0,
                'reduce_level': 0,
                'last_v': 0.0,
                'rising_count': 0,
            }
        st = self.state[ntc_ch]
        if st['reduce_level'] >= self.MAX_REDUCE_LEVELS:
            return
        new_slope = max(self.MIN_SLOPE, current_slope * factor)
        st['current_slope'] = new_slope
        st['reduce_level'] += 1
        st['rising_count'] = 0
        self.ntc_writer.write_cmd(ntc_ch, slope=new_slope)

    def check_re_reduce(self, ntc_ch: int, actual_v: float) -> bool:
        if ntc_ch not in self.state:
            return False
        st = self.state[ntc_ch]
        last_v = st['last_v']
        st['last_v'] = actual_v
        if actual_v > last_v:
            st['rising_count'] += 1
            if st['rising_count'] >= self.RE_REDUCE_THRESHOLD:
                if st['reduce_level'] < self.MAX_REDUCE_LEVELS:
                    self.reduce(ntc_ch, st['current_slope'])
                    return True
        else:
            st['rising_count'] = 0
        return False

    def try_restore(self, ntc_ch: int, actual_v: float, threshold: float):
        if ntc_ch not in self.state:
            return
        st = self.state[ntc_ch]
        if actual_v <= threshold:
            st['cycles_recovering'] += 1
            if st['cycles_recovering'] >= self.RESTORE_CYCLES:
                self.ntc_writer.write_cmd(ntc_ch, slope=st['original_slope'])
                del self.state[ntc_ch]
        else:
            st['cycles_recovering'] = 0
