import sys
import tracemalloc
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_response.ring_buffer import FrameRingBuffer


def pkt(i, ts, trace="t"):
    return L.SignalPacket(
        trace_id=f"{trace}-{i}", ts_ms=ts, source="signal",
        frame_id=i, width=640, height=480,
    )


class RingBufferTest(unittest.TestCase):
    def test_pre_event_exactly_2000ms_at_5fps(self):
        buf = FrameRingBuffer(capacity=50, pre_event_ms=2000)
        for i in range(1, 31):
            self.assertIsNone(buf.push(pkt(i, 1000 + (i - 1) * 200)))
        buf.mark_suspect()
        t0 = 1000 + 29 * 200
        buf.begin_violation(t0)
        pre = buf.pre_event_frames()
        self.assertEqual(len(pre), 11)
        self.assertEqual(pre[0], 20)
        self.assertEqual(pre[-1], 30)

    def test_clip_covers_pre_event_to_end(self):
        buf = FrameRingBuffer(capacity=50, pre_event_ms=2000)
        for i in range(1, 21):
            buf.push(pkt(i, i * 200))
        buf.mark_suspect()
        buf.begin_violation(4000)
        for i in range(21, 26):
            self.assertIsNone(buf.push(pkt(i, i * 200)))
        clip = buf.close_violation()
        self.assertEqual(clip[0], 10)
        self.assertEqual(clip[-1], 25)
        self.assertEqual(buf.phase, "CLOSED")

    def test_phase_sequence_and_false_alarm(self):
        buf = FrameRingBuffer(capacity=5, pre_event_ms=100)
        self.assertEqual(buf.phase, "WATCHING")
        buf.mark_suspect()
        buf.clear_suspect()
        self.assertEqual(buf.phase, "WATCHING")
        buf.mark_suspect()
        buf.begin_violation(10)
        buf.close_violation()
        buf.reset()
        self.assertEqual(buf.phase, "WATCHING")
        self.assertIsNone(buf.t0_ms)

    def test_invalid_transition_rejected(self):
        buf = FrameRingBuffer(capacity=5, pre_event_ms=100)
        with self.assertRaises(ValueError):
            buf.begin_violation(10)
        with self.assertRaises(ValueError):
            buf.close_violation()
        buf.mark_suspect()
        with self.assertRaises(ValueError):
            buf.close_violation()

    def test_no_memory_growth_100k_pushes(self):
        buf = FrameRingBuffer(capacity=50, pre_event_ms=2000)
        for i in range(1, 2001):
            buf.push(pkt(i, i * 40))
        tracemalloc.start()
        base, _ = tracemalloc.get_traced_memory()
        for i in range(2001, 102001):
            buf.push(pkt(i, i * 40))
        cur, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(buf.size, 50)
        self.assertLess(cur - base, 64 * 1024)

    def test_overrun_reported_when_event_frames_evicted(self):
        buf = FrameRingBuffer(capacity=3, pre_event_ms=100)
        for i in range(1, 4):
            buf.push(pkt(i, i * 50))
        buf.mark_suspect()
        buf.begin_violation(150)
        err = buf.push(pkt(4, 200))
        self.assertIsInstance(err, L.ErrorEvent)
        self.assertEqual(err.code, "E_RESP_BUFFER_OVERRUN")
        self.assertEqual(err.node_path, "response.ring_buffer")
        self.assertEqual(buf.overrun_count, 1)
        self.assertEqual(buf.health(), "DEGRADED")

    def test_bad_packets_return_error_events(self):
        buf = FrameRingBuffer(capacity=5, pre_event_ms=100)
        err = buf.push("không phải gói")
        self.assertEqual(err.code, "E_SIG_BAD_FORMAT")
        err = buf.push(L.SignalPacket(trace_id="", ts_ms=1, source="signal",
                                      frame_id=1, width=1, height=1))
        self.assertEqual(err.code, "E_SIG_BAD_FORMAT")
        buf.push(pkt(1, 100))
        err = buf.push(pkt(2, 50))
        self.assertEqual(err.code, "E_SIG_TS_DRIFT")
        self.assertEqual(buf.size, 1)

    def test_constructor_validation(self):
        with self.assertRaises(ValueError):
            FrameRingBuffer(capacity=0, pre_event_ms=100)
        with self.assertRaises(ValueError):
            FrameRingBuffer(capacity=5, pre_event_ms=-1)

    def test_ts_of_finds_frame_and_none_when_evicted(self):
        buf = FrameRingBuffer(capacity=3, pre_event_ms=100)
        for i in range(1, 5):
            buf.push(pkt(i, i * 50))
        self.assertEqual(buf.ts_of(4), 200)
        self.assertIsNone(buf.ts_of(1))
        self.assertIsNone(buf.ts_of(99))

    def test_self_test(self):
        ok, msg = FrameRingBuffer(capacity=50, pre_event_ms=2000).self_test()
        self.assertTrue(ok, msg)


if __name__ == "__main__":
    unittest.main()
