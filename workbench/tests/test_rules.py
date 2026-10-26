import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_process.rules.eye_drowsiness import EyeDrowsinessRule
from fd_process.rules.mouth_yawn import MouthYawnRule
from fd_process.rules.rules_engine import RulesEngine, RulesOutput
from fd_process.rules.util.label_vote import vote
from fd_process.rules.util.sustained import SustainedCondition

CLOSED = frozenset({"Eyeclosed", "closed", "close", "asleep", "Drowsy eye"})
OPEN = frozenset({"Attentive eye", "open"})
YAWN = frozenset({"Yawn", "yawn", "Open-Mouth"})
NO_YAWN = frozenset({"noYawn"})
STEP = 200


def det(i, ts, *pairs):
    return L.DetectionResult(
        trace_id=f"r-{i}", ts_ms=ts, source="process.ai", frame_id=i,
        items=tuple(L.Detection(label=l, conf=c) for l, c in pairs),
    )


def eye_rule(trigger=1500, gap=600):
    return EyeDrowsinessRule(CLOSED, OPEN, 0.5, trigger, gap)


class LabelVoteTest(unittest.TestCase):
    def test_highest_conf_wins_and_threshold(self):
        items = (L.Detection("closed", 0.9), L.Detection("open", 0.6))
        self.assertIs(vote(items, CLOSED, OPEN, 0.5), True)
        items = (L.Detection("closed", 0.4), L.Detection("open", 0.6))
        self.assertIs(vote(items, CLOSED, OPEN, 0.5), False)

    def test_tie_and_unrelated_are_unknown(self):
        items = (L.Detection("closed", 0.7), L.Detection("open", 0.7))
        self.assertIsNone(vote(items, CLOSED, OPEN, 0.5))
        self.assertIsNone(vote((L.Detection("cell phone", 0.9),), CLOSED, OPEN, 0.5))
        self.assertIsNone(vote((), CLOSED, OPEN, 0.5))


class SustainedTest(unittest.TestCase):
    def test_blink_under_threshold_never_triggers(self):
        c = SustainedCondition(trigger_ms=1500, max_gap_ms=600)
        phases = [c.observe(i * STEP, True).phase for i in range(2)]
        phases.append(c.observe(2 * STEP, False).phase)
        self.assertNotIn("TRIGGERED", phases)
        self.assertEqual(phases[-1], "IDLE")

    def test_trigger_once_then_active_then_end(self):
        c = SustainedCondition(trigger_ms=1000, max_gap_ms=600)
        seen = [c.observe(i * STEP, True).phase for i in range(10)]
        self.assertEqual(seen.count("TRIGGERED"), 1)
        self.assertEqual(seen[5], "TRIGGERED")
        self.assertEqual(seen[-1], "ACTIVE")
        end = c.observe(10 * STEP, False)
        self.assertEqual(end.phase, "ENDED")
        self.assertEqual(end.onset_ms, 0)

    def test_data_gap_restarts_and_never_triggers_from_missing_frames(self):
        c = SustainedCondition(trigger_ms=1000, max_gap_ms=600)
        c.observe(0, True)
        c.observe(200, True)
        st = c.observe(5000, True)
        self.assertEqual(st.phase, "SUSPECT")
        self.assertEqual(st.onset_ms, 5000)

    def test_unknown_frames_within_gap_keep_state_beyond_gap_reset(self):
        c = SustainedCondition(trigger_ms=1000, max_gap_ms=600)
        c.observe(0, True)
        self.assertEqual(c.observe(400, None).phase, "SUSPECT")
        self.assertEqual(c.observe(900, None).phase, "IDLE")

    def test_active_cut_by_gap_reports_end(self):
        c = SustainedCondition(trigger_ms=400, max_gap_ms=300)
        for i in range(4):
            c.observe(i * 200, True)
        st = c.observe(5000, None)
        self.assertEqual(st.phase, "ENDED")

    def test_constructor_validation(self):
        with self.assertRaises(ValueError):
            SustainedCondition(trigger_ms=0, max_gap_ms=100)
        with self.assertRaises(ValueError):
            SustainedCondition(trigger_ms=100, max_gap_ms=-1)


class EyeRuleTest(unittest.TestCase):
    def test_blink_not_reported(self):
        r = eye_rule()
        out = [r.observe(det(1, 0, ("closed", 0.9))).phase,
               r.observe(det(2, 200, ("closed", 0.9))).phase,
               r.observe(det(3, 400, ("open", 0.9))).phase]
        self.assertNotIn("TRIGGERED", out)

    def test_closed_over_threshold_triggers_with_onset(self):
        r = eye_rule()
        triggered = None
        for i in range(12):
            st = r.observe(det(i + 1, 1000 + i * STEP, ("Eyeclosed", 0.8)))
            if st.phase == "TRIGGERED":
                triggered = st
        self.assertIsNotNone(triggered)
        self.assertEqual(triggered.onset_ms, 1000)
        self.assertGreaterEqual(triggered.elapsed_ms, 1500)

    def test_every_closed_label_counts(self):
        for label in CLOSED:
            r = eye_rule(trigger=400)
            phases = [r.observe(det(i + 1, i * STEP, (label, 0.9))).phase for i in range(4)]
            self.assertIn("TRIGGERED", phases, label)

    def test_low_conf_or_unknown_does_not_trigger(self):
        r = eye_rule(trigger=400)
        phases = [r.observe(det(i + 1, i * STEP, ("closed", 0.3))).phase for i in range(10)]
        self.assertEqual(set(phases), {"IDLE"})

    def test_label_sets_must_not_overlap_or_be_empty(self):
        with self.assertRaises(ValueError):
            EyeDrowsinessRule(frozenset({"a"}), frozenset({"a"}), 0.5, 100, 100)
        with self.assertRaises(ValueError):
            EyeDrowsinessRule(frozenset(), frozenset({"a"}), 0.5, 100, 100)


class YawnRuleTest(unittest.TestCase):
    def test_yawn_triggers_after_threshold_only(self):
        r = MouthYawnRule(YAWN, NO_YAWN, 0.5, 2500, 600)
        phases = [r.observe(det(i + 1, i * STEP, ("yawn", 0.9))).phase for i in range(20)]
        first = phases.index("TRIGGERED")
        self.assertEqual(first, 13)

    def test_no_yawn_resets(self):
        r = MouthYawnRule(YAWN, NO_YAWN, 0.5, 1000, 600)
        for i in range(3):
            r.observe(det(i + 1, i * STEP, ("Yawn", 0.9)))
        st = r.observe(det(4, 600, ("noYawn", 0.9)))
        self.assertEqual(st.phase, "IDLE")


class EngineTest(unittest.TestCase):
    def make(self, history=50):
        return RulesEngine(
            [eye_rule(trigger=1000), MouthYawnRule(YAWN, NO_YAWN, 0.5, 2500, 600)],
            pre_event_ms=2000, history_frames=history,
        )

    def test_emits_single_violation_event_with_contract_fields(self):
        eng = self.make()
        events = []
        for i in range(15):
            out = eng.process(det(i + 1, 10_000 + i * STEP, ("closed", 0.9)))
            self.assertIsInstance(out, RulesOutput)
            events.extend(out.violations)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertIsInstance(ev, L.ViolationEvent)
        self.assertEqual(ev.rule, "eye_drowsiness")
        self.assertEqual(ev.t0_ms, 10_000)
        self.assertEqual(ev.pre_ms, 2000)
        self.assertEqual(ev.source, "process.rules")
        self.assertEqual(ev.frame_ids[0], 1)
        self.assertEqual(ev.frame_ids[-1], 6)

    def test_end_reported_when_eyes_open(self):
        eng = self.make()
        for i in range(8):
            eng.process(det(i + 1, i * STEP, ("closed", 0.9)))
        out = eng.process(det(9, 8 * STEP, ("open", 0.9)))
        self.assertEqual(out.ended, ("eye_drowsiness",))
        self.assertEqual(out.suspects, ())

    def test_independent_rules(self):
        eng = self.make()
        out = None
        for i in range(20):
            out = eng.process(det(i + 1, i * STEP, ("closed", 0.9), ("yawn", 0.9)))
        self.assertEqual(set(out.suspects), {"eye_drowsiness", "mouth_yawn"})

    def test_bad_inputs_return_error_events(self):
        eng = self.make()
        err = eng.process("không phải gói")
        self.assertEqual(err.code, "E_PROC_BAD_OUTPUT")
        self.assertEqual(err.node_path, "process.ai->process.rules")
        eng.process(det(1, 1000))
        err = eng.process(det(2, 500))
        self.assertEqual(err.code, "E_PROC_BAD_OUTPUT")
        self.assertEqual(eng.health(), "DEGRADED")
        self.assertIsInstance(eng.process(det(3, 1200)), RulesOutput)
        self.assertEqual(eng.health(), "OK")

    def test_constructor_validation_and_self_test(self):
        with self.assertRaises(ValueError):
            RulesEngine([], 2000, 10)
        with self.assertRaises(ValueError):
            RulesEngine([eye_rule(), eye_rule()], 2000, 10)
        ok, msg = self.make().self_test()
        self.assertTrue(ok, msg)


if __name__ == "__main__":
    unittest.main()
