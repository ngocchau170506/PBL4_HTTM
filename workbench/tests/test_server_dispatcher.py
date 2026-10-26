import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_response.server_dispatcher import (
    DispatchPackage,
    DispatchReceipt,
    ServerDispatcher,
    urllib_post_transport,
)


def make_event(idx: int = 1, frames=(10, 11, 12)) -> L.ViolationEvent:
    return L.ViolationEvent(
        trace_id=f"vio-{idx}",
        ts_ms=2000 + idx * 100,
        source="process.rules",
        rule="eye_drowsiness",
        t0_ms=1000 + idx * 100,
        pre_ms=2000,
        frame_ids=tuple(frames),
    )


class ServerDispatcherTest(unittest.TestCase):
    def test_enqueue_never_calls_transport_and_merges_clip_frames(self):
        called = []

        def transport(pkg: DispatchPackage, timeout_ms: int):
            called.append((pkg, timeout_ms))
            return True, f"ACK:{pkg.trace_id}"

        disp = ServerDispatcher(transport=transport, max_outbox=8, max_spool=8, timeout_ms=300)
        err = disp.enqueue(make_event(1, frames=(5, 6)), clip_frame_ids=(1, 2, 3, 4, 5, 6, 7))
        self.assertIsNone(err)
        self.assertEqual(len(called), 0)
        self.assertEqual(disp.outbox_size, 1)

        res = disp.flush_one()
        self.assertIsInstance(res, DispatchReceipt)
        self.assertEqual(res.frame_count, 7)
        self.assertEqual(res.ack, "ACK:vio-1")
        manifest = json.loads(called[0][0].to_manifest_json())
        self.assertEqual(manifest["frame_ids"], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(manifest["rule"], "eye_drowsiness")

    def test_connection_error_emits_e_resp_send_and_spools_package(self):
        def failing_transport(pkg: DispatchPackage, timeout_ms: int):
            raise OSError("Mất kết nối máy chủ")

        disp = ServerDispatcher(transport=failing_transport, max_outbox=4, max_spool=4)
        disp.enqueue(make_event(1))
        res = disp.flush_one()
        self.assertIsInstance(res, L.ErrorEvent)
        self.assertEqual(res.code, "E_RESP_SEND")
        self.assertEqual(res.node_path, "response.dispatcher")
        self.assertEqual(disp.spool_size, 1)
        self.assertEqual(disp.outbox_size, 0)
        self.assertEqual(disp.health(), "DEGRADED")

    def test_timeout_and_nack_emit_e_resp_no_ack(self):
        mode = {"state": "timeout"}

        def transport(pkg: DispatchPackage, timeout_ms: int):
            if mode["state"] == "timeout":
                raise TimeoutError("Server không phản hồi")
            return False, "HTTP_503:BUSY"

        disp = ServerDispatcher(transport=transport, max_outbox=4, max_spool=4)
        disp.enqueue(make_event(1))
        r1 = disp.flush_one()
        self.assertIsInstance(r1, L.ErrorEvent)
        self.assertEqual(r1.code, "E_RESP_NO_ACK")
        self.assertEqual(r1.node_path, "response.dispatcher")

        mode["state"] = "nack"
        r2 = disp.flush_one()
        self.assertIsInstance(r2, L.ErrorEvent)
        self.assertEqual(r2.code, "E_RESP_NO_ACK")
        self.assertEqual(disp.spool_size, 1)

    def test_circuit_breaker_opens_probes_single_packet_and_replays_when_online(self):
        online = {"up": False}
        attempts = []

        def transport(pkg: DispatchPackage, timeout_ms: int):
            attempts.append(pkg.trace_id)
            if not online["up"]:
                raise OSError("down")
            return True, f"OK:{pkg.trace_id}"

        disp = ServerDispatcher(
            transport=transport,
            max_outbox=10,
            max_spool=10,
            failure_threshold=2,
        )
        for i in range(1, 5):
            disp.enqueue(make_event(i))

        batch1 = disp.flush_all()
        self.assertEqual(len(batch1), 2)
        self.assertTrue(disp.circuit_open)
        self.assertEqual(disp.pending_count, 4)

        attempts.clear()
        batch2 = disp.flush_all()
        self.assertEqual(len(batch2), 1)
        self.assertEqual(len(attempts), 1)
        self.assertTrue(disp.circuit_open)

        online["up"] = True
        batch3 = disp.flush_all()
        self.assertEqual(len(batch3), 4)
        self.assertTrue(all(isinstance(x, DispatchReceipt) for x in batch3))
        self.assertFalse(disp.circuit_open)
        self.assertEqual(disp.health(), "OK")
        self.assertEqual(disp.pending_count, 0)
        self.assertEqual(disp.sent_count, 4)

    def test_bounded_outbox_and_spool_overflow_emits_e_resp_buffer_overrun(self):
        disp = ServerDispatcher(
            transport=lambda pkg, t: (False, "offline"),
            max_outbox=2,
            max_spool=2,
        )
        self.assertIsNone(disp.enqueue(make_event(1)))
        self.assertIsNone(disp.enqueue(make_event(2)))
        self.assertIsNone(disp.enqueue(make_event(3)))
        self.assertIsNone(disp.enqueue(make_event(4)))

        overrun = disp.enqueue(make_event(5))
        self.assertIsInstance(overrun, L.ErrorEvent)
        self.assertEqual(overrun.code, "E_RESP_BUFFER_OVERRUN")
        self.assertEqual(overrun.node_path, "response.dispatcher")
        self.assertEqual(disp.outbox_size, 2)
        self.assertEqual(disp.spool_size, 2)
        self.assertEqual(disp.dropped_count, 1)

    def test_bad_inputs_and_self_test(self):
        disp = ServerDispatcher(transport=lambda pkg, t: (True, "OK"))
        err1 = disp.enqueue("not-an-event")
        self.assertEqual(err1.code, "E_RESP_SEND")

        bad_ev = L.ViolationEvent(
            trace_id="",
            ts_ms=100,
            source="process.rules",
            rule="eye_drowsiness",
            t0_ms=50,
            pre_ms=2000,
            frame_ids=(1,),
        )
        err2 = disp.enqueue(bad_ev)
        self.assertEqual(err2.code, "E_RESP_SEND")

        with self.assertRaises(ValueError):
            ServerDispatcher(transport="not-callable")
        with self.assertRaises(ValueError):
            ServerDispatcher(transport=lambda p, t: (True, "OK"), max_outbox=0)
        with self.assertRaises(ValueError):
            urllib_post_transport("   ")

        ok, msg = disp.self_test()
        self.assertTrue(ok, msg)


if __name__ == "__main__":
    unittest.main()
