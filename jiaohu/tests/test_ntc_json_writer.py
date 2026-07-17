import sys
import os
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_coordinator.ntc_json_writer import NTCJsonWriter


class TestNTCJsonWriter(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.ntc_path = Path(self.tmp_dir) / 'NTC.json'
        initial = {
            "ntc_temps": [25.0] * 6,
            "slope": [5.0] * 6,
            "auto_run": [False] * 6,
        }
        with open(self.ntc_path, 'w', encoding='utf-8') as f:
            json.dump(initial, f)

        self.writer = NTCJsonWriter(self.ntc_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_write_cmd_single_channel(self):
        self.writer.write_cmd(1, temp=30.0)
        with open(self.ntc_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        self.assertEqual(cfg['_external_command']['0']['temp'], 30.0)

    def test_write_cmd_multi_channel_merge(self):
        self.writer.write_cmd(1, temp=30.0)
        self.writer.write_cmd(2, slope=2.0)
        with open(self.ntc_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        self.assertIn('0', cfg['_external_command'])
        self.assertIn('1', cfg['_external_command'])

    def test_write_mode(self):
        self.writer.write_mode('coordinator')
        with open(self.ntc_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        self.assertEqual(cfg['_mode'], 'coordinator')

    def test_write_reset_all_outputs(self):
        self.writer.write_reset_all_outputs()
        with open(self.ntc_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        for ch_idx in range(6):
            cmd = cfg['_external_command'][str(ch_idx)]
            self.assertEqual(cmd['temp'], 25.0)
            self.assertEqual(cmd['slope'], 0.0)
            self.assertFalse(cmd['auto_run'])

    def test_clear_commands(self):
        self.writer.write_cmd(1, temp=30.0)
        self.writer.clear_commands()
        with open(self.ntc_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        self.assertEqual(cfg.get('_external_command', {}), {})


if __name__ == '__main__':
    unittest.main()
