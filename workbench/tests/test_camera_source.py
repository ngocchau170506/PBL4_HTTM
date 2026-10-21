import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_connect.camera_source import (
    CameraSource,
    build_rtsp_low_latency_options,
    encode_raw_frame,
)


class CameraSourceTest(unittest.TestCase):
    def test_open_and_read_raw_packet(self):
        cam = CameraSource(source="webcam", width=640, height=480, fps=10)
        self.assertIsNone(cam.open(trace_id="t-open-01"))
        self.assertEqual(cam.health(), "OK")

        pkt = cam.read_packet(trace_id="t-read-01")
        self.assertIsInstance(pkt, L.RawPacket)
        self.assertEqual(pkt.trace_id, "t-read-01")
        self.assertEqual(pkt.source, "connect.camera")
        self.assertEqual(pkt.sensor_kind, "camera")
        self.assertTrue(pkt.payload.startswith(b"FDRW"))
        self.assertEqual(cam.packet_count, 1)

    def test_invalid_source_emits_e_conn_open(self):
        cam = CameraSource(source="invalid_cam")
        err = cam.open(trace_id="t-open-bad")
        self.assertIsInstance(err, L.ErrorEvent)
        self.assertEqual(err.code, "E_CONN_OPEN")
        self.assertEqual(err.node_path, "connect.camera")
        self.assertEqual(cam.health(), "DOWN")

    def test_rtsp_without_uri_emits_e_conn_open(self):
        cam = CameraSource(source="rtsp", uri=None)
        err = cam.open(trace_id="t-open-rtsp")
        self.assertIsInstance(err, L.ErrorEvent)
        self.assertEqual(err.code, "E_CONN_OPEN")
        self.assertEqual(err.node_path, "connect.camera")

    def test_simulate_disconnect_emits_e_conn_lost_with_node_path(self):
        cam = CameraSource(source="mock", width=640, height=480, fps=10)
        self.assertIsNone(cam.open(trace_id="t-open-02"))
        cam.simulate_disconnect()
        evt = cam.read_packet(trace_id="t-lost-01")
        self.assertIsInstance(evt, L.ErrorEvent)
        self.assertEqual(evt.code, "E_CONN_LOST")
        self.assertEqual(evt.node_path, "connect.camera")
        self.assertEqual(evt.trace_id, "t-lost-01")
        self.assertEqual(cam.health(), "DOWN")

    def test_simulate_timeout_emits_e_conn_timeout(self):
        cam = CameraSource(source="mock", width=640, height=480, fps=10)
        self.assertIsNone(cam.open(trace_id="t-open-03"))
        cam.simulate_timeout()
        evt = cam.read_packet(trace_id="t-timeout-01")
        self.assertIsInstance(evt, L.ErrorEvent)
        self.assertEqual(evt.code, "E_CONN_TIMEOUT")
        self.assertEqual(evt.node_path, "connect.camera")
        self.assertEqual(cam.health(), "DEGRADED")

    def test_self_test_and_rtsp_options(self):
        cam = CameraSource(source="mock")
        ok, msg = cam.self_test()
        self.assertTrue(ok)
        self.assertIn("CameraSource OK", msg)
        opts = build_rtsp_low_latency_options(2000000)
        self.assertIn("rtsp_transport;tcp", opts)
        self.assertIn("fflags;nobuffer", opts)
        raw = encode_raw_frame(320, 240, 7, 100)
        self.assertTrue(raw.startswith(b"FDRW"))


if __name__ == "__main__":
    unittest.main()
