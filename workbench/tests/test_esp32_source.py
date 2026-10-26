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

    def test_ascii_telemetry_line_parsing_and_normalization(self):
        ascii_line = b"CO2:435.5,ADC:1250,GPS_FIX:1,SPD:48.5,LAT:16.074,LON:108.223,SEQ:7\n"
        esp = Esp32Source(enabled=True, uri="COM3", gps_attached=True, packet_provider=lambda _: ascii_line)
        self.assertIsNone(esp.open(trace_id="esp-ascii-open"))
        raw = esp.read_packet(trace_id="esp-ascii-read")
        self.assertIsInstance(raw, L.RawPacket)
        assert isinstance(raw, L.RawPacket)
        self.assertTrue(raw.payload.startswith(b"FDESP:"))
        self.assertIsNotNone(esp.last_telemetry)
        assert esp.last_telemetry is not None
        self.assertEqual(esp.last_telemetry["co2_ppm"], 435.5)
        self.assertEqual(esp.last_telemetry["speed_kmh"], 48.5)

    def test_packet_loss_detection_via_sequence_gap(self):
        packets = [
            encode_esp32_telemetry(co2_ppm=400.0, seq=1),
            encode_esp32_telemetry(co2_ppm=405.0, seq=5),
        ]
        provider = lambda seq: packets[seq - 1] if seq <= len(packets) else None
        esp = Esp32Source(enabled=True, uri="COM3", gps_attached=True, packet_provider=provider)
        self.assertIsNone(esp.open(trace_id="loss-open"))
        esp.read_packet(trace_id="loss-read-1")
        self.assertEqual(esp.packet_loss_count, 0)
        esp.read_packet(trace_id="loss-read-2")
        self.assertEqual(esp.packet_loss_count, 3)

    def test_corrupted_payload_emits_e_sig_bad_format(self):
        esp = Esp32Source(enabled=True, uri="COM3", gps_attached=True, packet_provider=lambda _: b"INVALID_GARBAGE")
        self.assertIsNone(esp.open(trace_id="bad-open"))
        res = esp.read_packet(trace_id="bad-read")
        self.assertIsInstance(res, L.ErrorEvent)
        self.assertEqual(res.code, "E_SIG_BAD_FORMAT")
        self.assertEqual(esp.health(), "DEGRADED")

    def test_boot_noise_filtering_and_hardware_safety_flags(self):
        noisy_json = b"\xff\xfeets Jun 8 2016 00:22:57\r\nFDESP:{\"seq\":1,\"co2_ppm\":418.5,\"gps_ready\":1.0}\n"
        noisy_ascii = b"\x00\xffrst:0x1 (POWERON_RESET)CO2:440.0,ADC:1180,GPS_FIX:1,SPD:52.0,SEQ:2\r\n"
        stream = [noisy_json, noisy_ascii]
        esp = Esp32Source(
            enabled=True,
            uri="COM3",
            baudrate=115200,
            gps_attached=True,
            packet_provider=lambda seq: stream[seq - 1] if seq <= len(stream) else None,
        )
        self.assertFalse(esp.dsrdtr)
        self.assertFalse(esp.rtscts)
        self.assertEqual(esp.baudrate, 115200)
        self.assertIsNone(esp.open(trace_id="noise-open"))

        pkt1 = esp.read_packet(trace_id="noise-read-1")
        self.assertIsInstance(pkt1, L.RawPacket)
        assert esp.last_telemetry is not None
        self.assertEqual(esp.last_telemetry["co2_ppm"], 418.5)

        pkt2 = esp.read_packet(trace_id="noise-read-2")
        self.assertIsInstance(pkt2, L.RawPacket)
        assert esp.last_telemetry is not None
        self.assertEqual(esp.last_telemetry["co2_ppm"], 440.0)
        self.assertEqual(esp.last_telemetry["speed_kmh"], 52.0)

    def test_parse_1000_dual_format_telemetry_messages_and_reconnect_recovery(self):
        def dual_provider(seq: int) -> bytes:
            if seq % 2 == 1:
                return encode_esp32_telemetry(
                    co2_ppm=400.0 + (seq % 50),
                    vibration_g=0.03,
                    gps_attached=True,
                    gps_lat=16.074,
                    gps_lon=108.223,
                    speed_kmh=40.0,
                    seq=seq,
                )
            return (
                f"CO2:{400.0 + (seq % 50):.1f},ADC:1200,GPS_FIX:1,"
                f"SPD:45.0,LAT:16.074,LON:108.223,SEQ:{seq}\n"
            ).encode("utf-8")

        esp = Esp32Source(enabled=True, uri="COM3", gps_attached=True, packet_provider=dual_provider)
        self.assertIsNone(esp.open(trace_id="esp-1000-open"))

        for i in range(1, 1001):
            res = esp.read_packet(trace_id=f"esp-1000-{i}")
            self.assertIsInstance(res, L.RawPacket)

        self.assertEqual(esp.packet_count, 1000)
        self.assertEqual(esp.packet_loss_count, 0)
        self.assertEqual(esp.health(), "OK")

        esp.simulate_disconnect()
        lost = esp.read_packet(trace_id="esp-lost-after-1000")
        self.assertIsInstance(lost, L.ErrorEvent)
        assert isinstance(lost, L.ErrorEvent)
        self.assertEqual(lost.code, "E_CONN_LOST")
        self.assertEqual(esp.health(), "DOWN")

        self.assertIsNone(esp.reconnect(trace_id="esp-reconnect-ok"))
        self.assertEqual(esp.health(), "OK")
        recovered = esp.read_packet(trace_id="esp-read-after-reconnect")
        self.assertIsInstance(recovered, L.RawPacket)

    def test_self_test(self):
        esp = Esp32Source(enabled=True, uri="COM3", gps_attached=False)
        ok, detail = esp.self_test()
        self.assertTrue(ok)
        self.assertIn("Esp32Source OK", detail)


if __name__ == "__main__":
    unittest.main()
