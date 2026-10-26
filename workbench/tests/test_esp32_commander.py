import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from contracts.logic import ErrorEvent, RawPacket
from fd_connect.esp32_commander import AckReceipt, AlertCommand, Esp32Commander
from fd_connect.esp32_source import Esp32Source


class Esp32CommanderTest(unittest.TestCase):
    def test_format_alert_packets(self) -> None:
        commander = Esp32Commander()
        cmd_drowsy = AlertCommand(status="DROWSY", duration_ms=2000, level=1)
        pkt_std = commander.format_alert_packet(cmd_drowsy)
        self.assertTrue(pkt_std.startswith("ALERT,01,DROWSY,"))
        self.assertTrue(pkt_std.endswith(",2000\n"))

        cmd_yawn = AlertCommand(status="YAWNING", duration_ms=1000, level=2)
        pkt_yawn = commander.format_alert_packet(cmd_yawn)
        self.assertTrue(pkt_yawn.startswith("ALERT,02,YAWNING,"))

        pkt_legacy = commander.format_firmware_legacy_packet(cmd_drowsy)
        self.assertEqual(pkt_legacy, "ALERT:1:2000\n")

    def test_send_alert_success_with_ack(self) -> None:
        def mock_sender(packet: str) -> str:
            return "EVT,ACK,ALERT,1,i2s=1\n"

        commander = Esp32Commander(transport_sender=mock_sender)
        cmd = AlertCommand(status="DROWSY", duration_ms=1500, level=1, trace_id="cmd-t-01")
        receipt = commander.send_alert(cmd)

        self.assertIsInstance(receipt, AckReceipt)
        assert isinstance(receipt, AckReceipt)
        self.assertTrue(receipt.success)
        self.assertTrue(receipt.i2s_ready)
        self.assertEqual(commander.total_sent, 1)
        self.assertEqual(commander.total_acked, 1)
        self.assertEqual(commander.health(), "OK")

    def test_send_alert_timeout_emits_e_resp_no_ack(self) -> None:
        def timeout_sender(packet: str) -> None:
            return None

        commander = Esp32Commander(ack_timeout_ms=100, transport_sender=timeout_sender)
        cmd = AlertCommand(status="DROWSY", duration_ms=1500, level=1, trace_id="cmd-t-timeout")
        err = commander.send_alert(cmd)

        self.assertIsInstance(err, ErrorEvent)
        assert isinstance(err, ErrorEvent)
        self.assertEqual(err.code, "E_RESP_NO_ACK")
        self.assertEqual(commander.total_timeout, 1)
        self.assertEqual(commander.health(), "DEGRADED")

    def test_send_alert_invalid_ack_emits_e_resp_no_ack(self) -> None:
        def bad_ack_sender(packet: str) -> str:
            return "SOME_UNRECOGNIZED_OUTPUT\n"

        commander = Esp32Commander(transport_sender=bad_ack_sender)
        cmd = AlertCommand(status="DROWSY", duration_ms=1500, level=1, trace_id="cmd-t-bad-ack")
        err = commander.send_alert(cmd)

        self.assertIsInstance(err, ErrorEvent)
        assert isinstance(err, ErrorEvent)
        self.assertEqual(err.code, "E_RESP_NO_ACK")

    def test_mutex_concurrency_protection(self) -> None:
        shared_lock = threading.Lock()
        counter = {"active": 0, "max_active": 0}

        def concurrent_sender(packet: str) -> str:
            counter["active"] += 1
            if counter["active"] > counter["max_active"]:
                counter["max_active"] = counter["active"]
            time.sleep(0.01)
            counter["active"] -= 1
            return "EVT,ACK,ALERT,1,i2s=1"

        commander = Esp32Commander(serial_mutex=shared_lock, transport_sender=concurrent_sender)

        threads = []
        for i in range(5):
            t = threading.Thread(
                target=commander.send_alert,
                args=(AlertCommand(status="DROWSY", duration_ms=1000, level=1, trace_id=f"th-{i}"),),
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(commander.total_sent, 5)
        self.assertEqual(commander.total_acked, 5)
        # Nhờ có Mutex, tại một thời điểm active không bao giờ vượt quá 1
        self.assertEqual(counter["max_active"], 1)

    def test_bug_001_i2s_zero_flag_emits_error_and_sets_degraded(self) -> None:
        commander = Esp32Commander(transport_sender=lambda _: "EVT,ACK,ALERT,1,i2s=0\n")
        cmd = AlertCommand(status="DROWSY", duration_ms=1500, level=1, trace_id="cmd-bug001")
        res = commander.send_alert(cmd)
        self.assertIsInstance(res, ErrorEvent)
        assert isinstance(res, ErrorEvent)
        self.assertEqual(res.code, "E_RESP_NO_ACK")
        self.assertIn("i2s=0", res.message)
        self.assertEqual(commander.health(), "DEGRADED")

    def test_control_commands_and_non_blocking_command_queue(self) -> None:
        commander = Esp32Commander(max_queue=2)
        ping_res = commander.send_control("PING", trace_id="ctrl-ping")
        self.assertIsInstance(ping_res, AckReceipt)
        assert isinstance(ping_res, AckReceipt)
        self.assertEqual(ping_res.ack_payload, "PONG")

        stop_res = commander.send_control("STOP", trace_id="ctrl-stop")
        self.assertIsInstance(stop_res, AckReceipt)

        bad_ctrl = commander.send_control("UNKNOWN_CMD", trace_id="ctrl-bad")
        self.assertIsInstance(bad_ctrl, ErrorEvent)
        assert isinstance(bad_ctrl, ErrorEvent)
        self.assertEqual(bad_ctrl.code, "E_SIG_BAD_FORMAT")

        self.assertIsNone(commander.enqueue_alert(AlertCommand(status="DROWSY", trace_id="q-1")))
        self.assertIsNone(commander.enqueue_alert(AlertCommand(status="YAWNING", trace_id="q-2")))
        overrun = commander.enqueue_alert(AlertCommand(status="DROWSY", trace_id="q-3"))
        self.assertIsInstance(overrun, ErrorEvent)
        assert isinstance(overrun, ErrorEvent)
        self.assertEqual(overrun.code, "E_RESP_BUFFER_OVERRUN")
        self.assertEqual(commander.pending_count, 2)

        flushed = commander.flush_all()
        self.assertEqual(len(flushed), 2)
        self.assertEqual(commander.pending_count, 0)

    def test_shared_serial_mutex_between_telemetry_source_and_commander(self) -> None:
        shared_lock = threading.Lock()
        uart_state = {"in_use": 0, "max_concurrent": 0}

        def safe_telemetry_provider(seq: int) -> bytes:
            uart_state["in_use"] += 1
            if uart_state["in_use"] > uart_state["max_concurrent"]:
                uart_state["max_concurrent"] = uart_state["in_use"]
            time.sleep(0.002)
            uart_state["in_use"] -= 1
            return f"CO2:425.0,ADC:1200,GPS_FIX:1,SPD:45.0,SEQ:{seq}\n".encode("utf-8")

        def safe_command_sender(packet: str) -> str:
            uart_state["in_use"] += 1
            if uart_state["in_use"] > uart_state["max_concurrent"]:
                uart_state["max_concurrent"] = uart_state["in_use"]
            time.sleep(0.002)
            uart_state["in_use"] -= 1
            return "EVT,ACK,ALERT,1,i2s=1\n"

        esp_source = Esp32Source(
            enabled=True,
            uri="COM3",
            gps_attached=True,
            serial_mutex=shared_lock,
            packet_provider=safe_telemetry_provider,
        )
        esp_commander = Esp32Commander(
            enabled=True,
            serial_mutex=shared_lock,
            transport_sender=safe_command_sender,
        )
        self.assertIsNone(esp_source.open(trace_id="shared-open"))

        def reader_worker() -> None:
            for i in range(15):
                pkt = esp_source.read_packet(trace_id=f"r-{i}")
                self.assertIsInstance(pkt, RawPacket)

        def writer_worker() -> None:
            for i in range(15):
                ack = esp_commander.send_alert(
                    AlertCommand(status="DROWSY", duration_ms=1000, level=1, trace_id=f"w-{i}")
                )
                self.assertIsInstance(ack, AckReceipt)

        t_read = threading.Thread(target=reader_worker)
        t_write = threading.Thread(target=writer_worker)
        t_read.start()
        t_write.start()
        t_read.join()
        t_write.join()

        self.assertEqual(esp_source.packet_count, 15)
        self.assertEqual(esp_commander.total_acked, 15)
        self.assertEqual(uart_state["max_concurrent"], 1)

    def test_self_test(self) -> None:
        commander = Esp32Commander()
        ok, msg = commander.self_test()
        self.assertTrue(ok)
        self.assertIn("Esp32Commander OK", msg)


if __name__ == "__main__":
    unittest.main()
