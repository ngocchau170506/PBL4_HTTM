import sys
import typing
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L  # noqa: E402
from contracts import state as S  # noqa: E402


class ContractTest(unittest.TestCase):
    def test_flow_edges_use_known_nodes(self):
        known = set(typing.get_args(L.NodeId))
        for e in L.FLOW_TREE:
            self.assertIn(e.src, known)
            self.assertIn(e.dst, known)

    def test_system_transitions_cover_every_state(self):
        self.assertEqual(set(S.SYSTEM_TRANSITIONS), set(typing.get_args(S.SystemState)))

    def test_system_transition_targets_are_states(self):
        states = set(typing.get_args(S.SystemState))
        for targets in S.SYSTEM_TRANSITIONS.values():
            for t in targets:
                self.assertIn(t, states)

    def test_work_transitions_cover_every_state(self):
        self.assertEqual(set(S.WORK_TRANSITIONS), set(typing.get_args(S.WorkState)))


if __name__ == "__main__":
    unittest.main()
