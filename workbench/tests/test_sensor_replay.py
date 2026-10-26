import sys
import tracemalloc
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from contracts.logic import ErrorEvent, RawPacket
from fd_connect.esp32_source import Esp32Source
from fd_connect.sensor_replay import (
    SensorReplay,
    format_ascii_telemetry_bytes,
    format_telemetry_bytes,
)


class SensorReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_file = ROOT / "workbench" / "fixtures" / "telemetry_sample.jsonl"

    def test_format_telemetry_bytes(self) -> None:
        payload = format_telemetry_bytes(
            seq=10,
            co2_ppm=450.5,
            vibration_g=0.03,
            gps_ready=1.0,
            gps_lat=16.074,
            gps_lon=108.223,
            speed_kmh=50.2,
        )
        self.assertTrue(payload.startswith(b"FDESP:"))
        self.assertIn(b"450.5", payload)
        self.assertIn(b"16.074", payload)

    def test_load_and_replay_from_fixture(self) -> None:
        replay = SensorReplay(fixture_path=self.fixture_file, loop=False)
        self.assertGreater(replay.total_records, 0)

        first_pkt = replay.next_packet_payload(seq=1)
        self.assertIsNotNone(first_pkt)
        assert first_pkt is not None
        self.assertTrue(first_pkt.startswith(b"FDESP:"))
        self.assertEqual(replay.emitted_count, 1)

    def test_replay_loop_mode(self) -> None:
        replay = SensorReplay(fixture_path=self.fixture_file, loop=True)
        total = replay.total_records
        for i in range(total * 2):
            pkt = replay.next_packet_payload(seq=i + 1)
            self.assertIsNotNone(pkt)
        self.assertEqual(replay.emitted_count, total * 2)

    def test_replay_non_loop_mode_stops_at_end(self) -> None:
        replay = SensorReplay(fixture_path=self.fixture_file, loop=False)
        total = replay.total_records
        for i in range(total):
            pkt = replay.next_packet_payload(seq=i + 1)
            self.assertIsNotNone(pkt)
        ended_pkt = replay.next_packet_payload(seq=total + 1)
        self.assertIsNone(ended_pkt)

    def test_fault_injection_drop(self) -> None:
        replay = SensorReplay(fixture_path=self.fixture_file, loop=True, drop_every_n=3)
        p1 = replay.next_packet_payload(seq=1)
        p2 = replay.next_packet_payload(seq=2)
        p3_dropped = replay.next_packet_payload(seq=3)
        self.assertIsNotNone(p1)
        self.assertIsNotNone(p2)
        self.assertIsNone(p3_dropped)
        self.assertEqual(replay.dropped_count, 1)

    def test_fault_injection_corrupt(self) -> None:
        replay = SensorReplay(fixture_path=self.fixture_file, loop=True, corrupt_every_n=2)
        p1 = replay.next_packet_payload(seq=1)
        p2_corrupt = replay.next_packet_payload(seq=2)
        self.assertIsNotNone(p1)
        self.assertIsNotNone(p2_corrupt)
        assert p2_corrupt is not None
        self.assertIn(b"corrupted_json", p2_corrupt)
        self.assertEqual(replay.corrupted_count, 1)

    def test_integration_with_esp32_source(self) -> None:
        replay = SensorReplay(fixture_path=self.fixture_file, loop=True)
        esp = Esp32Source(enabled=True, uri="COM3", gps_attached=True, packet_provider=replay.next_packet_payload)
        open_err = esp.open(trace_id="rep-t-open")
        self.assertIsNone(open_err)

        raw = esp.read_packet(trace_id="rep-t-read-1")
        self.assertIsInstance(raw, RawPacket)
        assert isinstance(raw, RawPacket)
        self.assertEqual(raw.sensor_kind, "esp32")
        self.assertTrue(raw.payload.startswith(b"FDESP:"))

    def test_replay_50000_packets_zero_ram_leak_and_ascii_mode(self) -> None:
        ascii_bytes = format_ascii_telemetry_bytes(seq=1, co2_ppm=430.0, speed_kmh=55.0)
        self.assertIn(b"CO2:430.0", ascii_bytes)
        self.assertIn(b"SPD:55.0", ascii_bytes)

        replay = SensorReplay(fixture_path=self.fixture_file, loop=True, output_ascii=True)
        tracemalloc.start()
        for seq in range(1, 50001):
            pkt = replay.next_packet_payload(seq=seq)
            self.assertIsNotNone(pkt)
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        self.assertEqual(replay.emitted_count, 50000)
        # 50,000 gói không giữ tham chiếu trong RAM (current_mem < 128 KB)
        self.assertLess(current_mem, 128 * 1024)
        self.assertLess(peak_mem, 512 * 1024)


if __name__ == "__main__":
    unittest.main()
