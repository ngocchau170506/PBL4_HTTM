import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench"))
from probes import probe_runner as P  # noqa: E402


class GoodNode:
    node_id = "signal" 

    def health(self):
        return "OK"

    def self_test(self):
        return True, "ok"


class BadIdNode(GoodNode):
    node_id = "nope"


class BadHealthNode(GoodNode):
    def health(self):
        return "FINE"


class RaisingNode(GoodNode):
    def self_test(self):
        raise RuntimeError("boom")


class BadReturnNode(GoodNode):
    def self_test(self):
        return "yes"


class DownNode(GoodNode):
    def health(self):
        return "DOWN"


class ProbeRunnerTest(unittest.TestCase):
    def test_good_node_passes(self):
        r = P.run_probe(GoodNode())
        self.assertTrue(r["pass"])
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["node_id"], "signal")

    def test_each_interface_violation_is_reported(self):
        cases = {BadIdNode: "node_id", BadHealthNode: "health()", RaisingNode: "boom",
                 BadReturnNode: "(bool, str)"}
        for cls, needle in cases.items():
            with self.subTest(cls=cls.__name__):
                r = P.run_probe(cls())
                self.assertFalse(r["pass"])
                self.assertTrue(any(needle in p for p in r["problems"]), r["problems"])

    def test_down_node_fails_even_if_self_test_ok(self):
        r = P.run_probe(DownNode())
        self.assertEqual(r["problems"], [])
        self.assertFalse(r["pass"])

    def test_run_all_fails_if_any_fails(self):
        self.assertTrue(P.run_all([GoodNode()])["pass"])
        self.assertFalse(P.run_all([GoodNode(), RaisingNode()])["pass"])

    def test_known_nodes_match_contracts(self):
        self.assertIn("process.ai", P.KNOWN_NODES)


if __name__ == "__main__":
    unittest.main()
