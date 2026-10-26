import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_connect.camera_source import CameraSource
from fd_flow.flow_engine import FlowEngine
from fd_process.rules.eye_drowsiness import EyeDrowsinessRule
from fd_process.rules.rules_engine import RulesEngine
from fd_response.ring_buffer import FrameRingBuffer
from fd_response.server_dispatcher import DispatchReceipt, ServerDispatcher

CLOSED = frozenset({"closed"})
OPEN = frozenset({"open"})
TS_OFFSET = 5_000_000


class ScriptedAdapter:
    """Trả nhãn theo thứ tự khung được lấy mẫu; giờ suy luận lệch hẳn giờ khung để kiểm khóa frame_id."""

    name = "scripted"

    def __init__(self, closed_ordinals, ts_fn=None):
        self._closed = set(closed_ordinals)
        self._ts_fn = ts_fn or (lambda n, frame: frame.ts_ms + TS_OFFSET)
        self.frame_ids = []

    def load(self):
        return None

    def health(self):
        return "OK"

    def infer(self, frame):
        n = len(self.frame_ids)
        self.frame_ids.append(frame.frame_id)
        label = "closed" if n in self._closed else "open"
        return L.DetectionResult(
            trace_id=frame.trace_id, ts_ms=self._ts_fn(n, frame), source="process.ai",
            frame_id=frame.frame_id, items=(L.Detection(label, 0.9),),
        )


def build(adapter, ring_capacity=50, trigger_ms=1000):
    rules = RulesEngine(
        [EyeDrowsinessRule(CLOSED, OPEN, 0.5, trigger_ms, 600)],
        pre_event_ms=2000, history_frames=100,
    )
    engine = FlowEngine(
        camera=CameraSource(source="mock", index=0, uri=None, fps=25, width=640, height=480),
        ai_adapter=adapter,
        rules_engine=rules,
        ring_buffer=FrameRingBuffer(capacity=ring_capacity, pre_event_ms=2000),
    )
    assert engine.boot(trace_id="fr-boot") is None
    return engine


def run(engine, sampled_count):
    results = []
    i = 0
    while len(results) < sampled_count:
        res = engine.step_sampled(
            trace_id=f"fr-{i + 1}", driver_id="drv-01", ts_override_ms=1000 + i * 40,
        )
        if res is not None:
            results.append(res)
        i += 1
    return results


def vio_logs(engine):
    return [x for x in engine.response.logs() if "RESP-VIO" in x]


class FlowRulesTest(unittest.TestCase):
    def test_violation_flows_through_rules_edge_with_pre_event_frames(self):
        adapter = ScriptedAdapter(range(12, 22))
        engine = build(adapter)
        results = run(engine, 26)
        for r in results:
            self.assertIsInstance(r, L.DetectionResult)

        logs = vio_logs(engine)
        self.assertEqual(len(logs), 1)
        self.assertIn("rule=eye_drowsiness", logs[0])
        self.assertIn("t0_ms=3400", logs[0])
        self.assertIn("frames=16", logs[0])
        self.assertFalse([x for x in engine.response.logs() if "RESP-DET" in x])

        fids = adapter.frame_ids
        clip = engine.last_clip
        self.assertIsNotNone(clip)
        self.assertEqual(len(clip), 21)
        self.assertEqual(clip[0], fids[2])
        self.assertEqual(clip[-1], fids[22])
        self.assertEqual(engine.ring_buffer.phase, "WATCHING")
        self.assertEqual(engine.queues.depth("process.rules", "response"), 0)
        self.assertEqual(engine.queues.depth("process.ai", "process.rules"), 0)

    def test_blink_produces_no_violation_and_buffer_returns_to_watching(self):
        engine = build(ScriptedAdapter([12, 13]))
        run(engine, 20)
        self.assertEqual(vio_logs(engine), [])
        self.assertEqual(engine.response.processed_count, 0)
        self.assertEqual(engine.ring_buffer.phase, "WATCHING")
        self.assertIsNone(engine.last_clip)

    def test_rules_error_is_dispatched_with_edge_node_path(self):
        def ts_fn(n, frame):
            return frame.ts_ms + TS_OFFSET - (10_000 if n == 3 else 0)

        engine = build(ScriptedAdapter([], ts_fn=ts_fn))
        seen = None
        i = 0
        sampled = 0
        while sampled < 6:
            res = engine.step_sampled(
                trace_id=f"fe-{i + 1}", driver_id="drv-01", ts_override_ms=1000 + i * 40,
            )
            i += 1
            if res is None:
                continue
            sampled += 1
            if isinstance(res, L.ErrorEvent):
                seen = res
        self.assertIsNotNone(seen)
        self.assertEqual(seen.code, "E_PROC_BAD_OUTPUT")
        self.assertEqual(seen.node_path, "process.ai->process.rules")
        self.assertEqual(engine.diagnose.latest().node_path, "process.ai->process.rules")

    def test_event_frames_evicted_reports_overrun_but_event_still_sent(self):
        engine = build(ScriptedAdapter(range(12, 22)), ring_capacity=3)
        i = 0
        sampled = 0
        reported = None
        while sampled < 20 and reported is None:
            res = engine.step_sampled(
                trace_id=f"fo-{i + 1}", driver_id="drv-01", ts_override_ms=1000 + i * 40,
            )
            i += 1
            if res is None:
                continue
            sampled += 1
            self.assertIsInstance(res, L.DetectionResult)
            if vio_logs(engine):
                reported = engine.diagnose.latest()
        self.assertIsNotNone(reported)
        self.assertEqual(reported.code, "E_RESP_BUFFER_OVERRUN")
        self.assertEqual(reported.node_path, "response.ring_buffer")
        self.assertEqual(len(vio_logs(engine)), 1)

    def test_without_rules_engine_keeps_previous_behavior(self):
        engine = FlowEngine(
            camera=CameraSource(source="mock", index=0, uri=None, fps=25, width=640, height=480),
            ai_adapter=ScriptedAdapter([]),
        )
        self.assertIsNone(engine.boot(trace_id="fr-old"))
        res = engine.step_once(trace_id="fr-old-1")
        self.assertIsInstance(res, L.DetectionResult)
        self.assertEqual(engine.response.processed_count, 1)
        self.assertIsNone(engine.rules)
        self.assertIsNone(engine.ring_buffer)

    def test_enable_rules_from_config_and_server_dispatcher_end_to_end(self):
        adapter = ScriptedAdapter(range(12, 24))
        disp = ServerDispatcher(transport=lambda pkg, t: (True, f"ACK:{pkg.trace_id}"))
        engine = FlowEngine(
            camera=CameraSource(source="mock", index=0, uri=None, fps=25, width=640, height=480),
            ai_adapter=adapter,
            server_dispatcher=disp,
            enable_rules_from_config=True,
        )
        self.assertIsNone(engine.boot(trace_id="fr-cfg-e2e"))
        self.assertIsNotNone(engine.rules)
        self.assertIsNotNone(engine.ring_buffer)
        self.assertIs(engine.dispatcher, disp)

        run(engine, 25)
        self.assertEqual(disp.outbox_size, 1)
        receipt = disp.flush_one()
        self.assertIsInstance(receipt, DispatchReceipt)
        self.assertEqual(receipt.rule, "eye_drowsiness")
        self.assertEqual(receipt.frame_count, 19)
        self.assertEqual(disp.sent_count, 1)


if __name__ == "__main__":
    unittest.main()
