"""No Home Assistant installation is required to test host-side Modbus timing."""

import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "custom_components" / "fronius_modbus" / "load_sample_timing.py"
spec = importlib.util.spec_from_file_location("load_sample_timing", MODULE)
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)


class TestLoadSampleTiming(unittest.TestCase):
    def test_real_world_660ms_sequential_requests_accepted(self):
        now = 101.34
        inverter = (100.0, 100.66)
        meter = (100.68, 101.34)
        self.assertIsNone(timing.load_window_issue(inverter, now))
        self.assertIsNone(timing.load_window_issue(meter, now))
        self.assertIsNone(timing.load_pair_issue(inverter, meter))

    def test_normal_short_requests_accepted(self):
        self.assertIsNone(timing.load_window_issue((9.6, 10.0), 10.0))
        self.assertIsNone(timing.load_window_issue((10.01, 10.42), 10.42))

    def test_slow_single_request_rejected(self):
        self.assertEqual(timing.load_window_issue((10.0, 12.1), 12.1), "Modbus response too slow")

    def test_long_modbus_timeout_rejected(self):
        self.assertEqual(timing.load_window_issue((100.0, 154.568), 154.568), "Modbus response too slow")

    def test_overly_separated_reads_rejected(self):
        inv = (10.0, 11.5)
        meter = (13.3, 14.0)
        self.assertEqual(timing.load_pair_issue(inv, meter), "inverter/meter requests too far apart")
        self.assertEqual(timing.load_window_issue(inv, 14.0), "outdated request window")

    def test_read_time_at_limit_accepted(self):
        self.assertIsNone(timing.load_window_issue((10.0, 12.0), 12.0))

    def test_total_snapshot_at_limit_accepted(self):
        self.assertIsNone(timing.load_pair_issue((10.0, 10.7), (12.4, 13.0)))
        self.assertIsNone(timing.load_window_issue((10.0, 10.7), 13.0))

    def test_invalid_windows_rejected(self):
        cases = [None, (), (10.0,), (10.0, 9.0), (10.0, 10.1, 10.2),
                 (float('nan'), 10.0), (10.0, float('inf')), (10.0, 10.5)]
        for window in cases:
            with self.subTest(window=window):
                self.assertIsNotNone(timing.load_window_issue(window, 10.0 if window != (10.0, 10.5) else 9.0))

    def test_stale_completed_window_rejected(self):
        self.assertEqual(timing.load_window_issue((10.0, 10.6), 15.0), "outdated request window")

    def test_boolean_timestamps_rejected(self):
        self.assertEqual(timing.load_window_issue((True, 11.0), 11.0), "invalid request timestamps")


if __name__ == "__main__":
    unittest.main()
