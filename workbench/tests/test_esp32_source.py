import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_connect.camera_source import encode_raw_frame
from fd_connect.esp32_source import Esp32Source, encode_esp32_telemetry
from fd_flow.flow_engine import FlowEngine
from fd_signal.frame_normalizer import FrameNormalizer


class Esp32SourceG25Test(unittest.TestCase):
    def test_open_com3_without_gps_sets_degraded_and_emits_telemetry(self):
        esp = Esp32Source(enabled=True, uri="COM3", gps_attached=False)
        self.assertIsNone(esp.open(trace_id="esp-t-01"))
        self.assertEqual(esp.health(), "DEGRADED")

        pkt = esp.read_packet(trace_id="esp-t-read-01")
        self.assertIsInstance(pkt, L.RawPacket)
        self.assertEqual(pkt.source, "connect.esp32")
        self.assertEqual(pkt.sensor_kind, "esp32")
        self.assertTrue(pkt.payload.startswith(b"FDESP:"))

        gps_err = esp.check_gps_ready(trace_id="esp-t-gps")
        self.assertIsInstance(gps_err, L.ErrorEvent)
        self.assertEqual(gps_err.node_path, "connect.esp32")

    def test_normalizer_ingests_esp32_and_attaches_to_signal_packet(self):
        norm = FrameNormalizer(expected_width=640, expected_height=480)
        esp_raw = L.RawPacket(
            trace_id="esp-sig-01",
            ts_ms=1000,
            source="connect.esp32",
            payload=encode_esp32_telemetry(co2_ppm=650.5, vibration_g=0.12, gps_attached=False, seq=1),
            sensor_kind="esp32",
        )
        sensor_dict = norm.ingest_esp32_packet(esp_raw)
        self.assertIsInstance(sensor_dict, dict)
        self.assertEqual(sensor_dict["co2_ppm"], 650.5)
        self.assertEqual(sensor_dict["gps_ready"], 0.0)

        cam_raw = L.RawPacket(
            trace_id="cam-sig-01",
            ts_ms=1040,
            source="connect.camera",
            payload=encode_raw_frame(640, 480, 1, luma_mean=130),
            sensor_kind="camera",
        )
        sig_pkt = norm.normalize(cam_raw, driver_id="drv-01")
        self.assertIsInstance(sig_pkt, L.SignalPacket)
        self.assertIsNotNone(sig_pkt.sensor)
        assert sig_pkt.sensor is not None
        self.assertEqual(sig_pkt.sensor["co2_ppm"], 650.5)

    def test_flow_engine_esp32_degraded_or_lost_does_not_stop_camera(self):
        esp = Esp32Source(enabled=True, uri="COM3", gps_attached=False)
        engine = FlowEngine(esp32=esp)
        self.assertIsNone(engine.boot(trace_id="flow-g25-boot"))

        sensor_res = engine.poll_esp32(trace_id="flow-g25-poll")
        self.assertIsInstance(sensor_res, dict)
        self.assertEqual(engine.breaker.system_state, "DEGRADED")

        esp.simulate_disconnect()
        lost_res = engine.poll_esp32(trace_id="flow-g25-lost")
        self.assertIsInstance(lost_res, L.ErrorEvent)
        self.assertEqual(lost_res.code, "E_CONN_LOST")
        self.assertEqual(lost_res.node_path, "connect.esp32")

        det = engine.step_sampled(
            trace_id="flow-g25-cam-ok",
            driver_id="drv-g25",
            ts_override_ms=2000,
        )
        self.assertIsInstance(det, L.DetectionResult)

    def test_self_test(self):
        esp = Esp32Source(enabled=True, uri="COM3", gps_attached=False)
        ok, detail = esp.self_test()
        self.assertTrue(ok)
        self.assertIn("Esp32Source OK", detail)


if __name__ == "__main__":
    unittest.main()
