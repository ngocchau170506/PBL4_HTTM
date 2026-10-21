import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_connect.camera_source import encode_raw_frame
from fd_signal.frame_normalizer import FrameNormalizer


class FrameNormalizerTest(unittest.TestCase):
    def test_valid_raw_packet_produces_signal_packet(self):
        norm = FrameNormalizer(expected_width=640, expected_height=480, default_driver_id="drv-default")
        raw = L.RawPacket(
            trace_id="sig-t-01",
            ts_ms=1000,
            source="connect.camera",
            payload=encode_raw_frame(640, 480, 1, luma_mean=150),
            sensor_kind="camera",
        )
        sig = norm.normalize(raw)
        self.assertIsInstance(sig, L.SignalPacket)
        self.assertEqual(sig.trace_id, "sig-t-01")
        self.assertEqual(sig.frame_id, 1)
        self.assertEqual(sig.width, 640)
        self.assertEqual(sig.height, 480)
        self.assertEqual(sig.driver_id, "drv-default")
        self.assertEqual(norm.health(), "OK")

    def test_empty_or_black_frame_emits_e_sig_empty(self):
        norm = FrameNormalizer(expected_width=640, expected_height=480)
        empty_raw = L.RawPacket(
            trace_id="sig-t-empty",
            ts_ms=1000,
            source="connect.camera",
            payload=b"",
            sensor_kind="camera",
        )
        evt1 = norm.normalize(empty_raw)
        self.assertIsInstance(evt1, L.ErrorEvent)
        self.assertEqual(evt1.code, "E_SIG_EMPTY")
        self.assertEqual(evt1.node_path, "signal")

        black_raw = L.RawPacket(
            trace_id="sig-t-black",
            ts_ms=1100,
            source="connect.camera",
            payload=encode_raw_frame(640, 480, 2, luma_mean=0),
            sensor_kind="camera",
        )
        evt2 = norm.normalize(black_raw)
        self.assertIsInstance(evt2, L.ErrorEvent)
        self.assertEqual(evt2.code, "E_SIG_EMPTY")
        self.assertEqual(evt2.node_path, "signal")

    def test_bad_size_or_bad_header_emits_e_sig_bad_format(self):
        norm = FrameNormalizer(expected_width=640, expected_height=480)
        bad_size = L.RawPacket(
            trace_id="sig-t-size",
            ts_ms=1000,
            source="connect.camera",
            payload=encode_raw_frame(800, 600, 1, luma_mean=100),
            sensor_kind="camera",
        )
        evt1 = norm.normalize(bad_size)
        self.assertIsInstance(evt1, L.ErrorEvent)
        self.assertEqual(evt1.code, "E_SIG_BAD_FORMAT")
        self.assertEqual(evt1.node_path, "signal")

        bad_magic = L.RawPacket(
            trace_id="sig-t-magic",
            ts_ms=1100,
            source="connect.camera",
            payload=b"XXXX0123456789abcdef",
            sensor_kind="camera",
        )
        evt2 = norm.normalize(bad_magic)
        self.assertIsInstance(evt2, L.ErrorEvent)
        self.assertEqual(evt2.code, "E_SIG_BAD_FORMAT")

    def test_timestamp_drift_emits_e_sig_ts_drift(self):
        norm = FrameNormalizer(expected_width=640, expected_height=480, max_drift_ms=1000)
        raw1 = L.RawPacket(
            trace_id="sig-t-ts1",
            ts_ms=2000,
            source="connect.camera",
            payload=encode_raw_frame(640, 480, 1, luma_mean=120),
            sensor_kind="camera",
        )
        self.assertIsInstance(norm.normalize(raw1), L.SignalPacket)

        raw_backward = L.RawPacket(
            trace_id="sig-t-ts2",
            ts_ms=1800,
            source="connect.camera",
            payload=encode_raw_frame(640, 480, 2, luma_mean=120),
            sensor_kind="camera",
        )
        evt = norm.normalize(raw_backward)
        self.assertIsInstance(evt, L.ErrorEvent)
        self.assertEqual(evt.code, "E_SIG_TS_DRIFT")
        self.assertEqual(evt.node_path, "signal")

    def test_self_test(self):
        norm = FrameNormalizer()
        ok, detail = norm.self_test()
        self.assertTrue(ok)
        self.assertIn("FrameNormalizer OK", detail)


if __name__ == "__main__":
    unittest.main()
