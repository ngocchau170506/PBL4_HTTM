import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_process.sampler.fps_throttle import FpsThrottle


class FpsThrottleTest(unittest.TestCase):
    def test_rtsp_25fps_throttled_to_5fps_exact(self):
        throttle = FpsThrottle(target_fps=5)
        passed_frames = []
        for i in range(250):
            pkt = L.SignalPacket(
                trace_id=f"smp-25-{i + 1}",
                ts_ms=1000 + i * 40,
                source="signal",
                frame_id=i + 1,
                width=640,
                height=480,
                driver_id="drv-01",
            )
            res = throttle.filter_packet(pkt)
            if res is not None:
                self.assertIsInstance(res, L.SignalPacket)
                self.assertEqual(res.source, "process.sampler")
                passed_frames.append(res)

        self.assertEqual(len(passed_frames), 50)
        st = throttle.stats()
        self.assertEqual(st.total_in, 250)
        self.assertEqual(st.total_passed, 50)
        self.assertEqual(st.total_dropped, 200)
        self.assertAlmostEqual(st.pass_ratio, 0.2, places=3)
        self.assertEqual(throttle.health(), "OK")

    def test_30fps_throttled_to_6fps_with_integer_ms_rounding(self):
        throttle = FpsThrottle(target_fps=6)
        passed_count = 0
        for i in range(300):
            ts_ms = 5000 + round(i * (1000.0 / 30.0))
            pkt = L.SignalPacket(
                trace_id=f"smp-30-{i + 1}",
                ts_ms=ts_ms,
                source="signal",
                frame_id=i + 1,
                width=640,
                height=480,
                driver_id="drv-01",
            )
            res = throttle.filter_packet(pkt)
            if res is not None:
                self.assertIsInstance(res, L.SignalPacket)
                passed_count += 1

        self.assertEqual(passed_count, 60)

    def test_backward_timestamp_emits_e_sig_ts_drift(self):
        throttle = FpsThrottle(target_fps=5)
        p1 = L.SignalPacket("smp-ts-1", 3000, "signal", 1, 640, 480)
        p2 = L.SignalPacket("smp-ts-2", 2900, "signal", 2, 640, 480)
        self.assertIsInstance(throttle.filter_packet(p1), L.SignalPacket)
        err = throttle.filter_packet(p2)
        self.assertIsInstance(err, L.ErrorEvent)
        self.assertEqual(err.code, "E_SIG_TS_DRIFT")
        self.assertEqual(err.node_path, "process.sampler")
        self.assertEqual(throttle.health(), "DEGRADED")

    def test_missing_trace_or_invalid_target_fps_emits_error_event(self):
        bad_cfg = FpsThrottle(target_fps=0)
        self.assertEqual(bad_cfg.health(), "DOWN")
        p1 = L.SignalPacket("smp-cfg", 1000, "signal", 1, 640, 480)
        err_cfg = bad_cfg.filter_packet(p1)
        self.assertIsInstance(err_cfg, L.ErrorEvent)
        self.assertEqual(err_cfg.code, "E_CFG_INVALID")
        self.assertEqual(err_cfg.node_path, "process.sampler")

        throttle = FpsThrottle(target_fps=5)
        bad_pkt = L.SignalPacket("", 1000, "signal", 1, 640, 480)
        err_trace = throttle.filter_packet(bad_pkt)
        self.assertIsInstance(err_trace, L.ErrorEvent)
        self.assertEqual(err_trace.code, "E_SIG_BAD_FORMAT")
        self.assertEqual(err_trace.node_path, "process.sampler")

    def test_pause_recovery_does_not_burst_frames(self):
        throttle = FpsThrottle(target_fps=5)
        p1 = L.SignalPacket("smp-p1", 1000, "signal", 1, 640, 480)
        self.assertIsNotNone(throttle.filter_packet(p1))

        p_after_pause = L.SignalPacket("smp-p2", 5000, "signal", 2, 640, 480)
        self.assertIsNotNone(throttle.filter_packet(p_after_pause))

        p_immediate = L.SignalPacket("smp-p3", 5040, "signal", 3, 640, 480)
        self.assertIsNone(throttle.filter_packet(p_immediate))

    def test_self_test_and_reset(self):
        throttle = FpsThrottle(target_fps=5)
        ok, detail = throttle.self_test()
        self.assertTrue(ok)
        self.assertIn("FpsThrottle OK", detail)
        throttle.reset()
        self.assertEqual(throttle.total_in, 0)
        self.assertEqual(throttle.total_passed, 0)


if __name__ == "__main__":
    unittest.main()
