import json
import sys
import tempfile
import unittest
from pathlib import Path

WB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WB))
import check_scope as cs  # noqa: E402


def make_project(files, allowed):
    d = Path(tempfile.mkdtemp())
    (d / "scope").mkdir()
    (d / "scope" / "G0.json").write_text(
        json.dumps({"gate": "G0", "allowed": allowed}), encoding="utf-8"
    )
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return d


class ScopeTest(unittest.TestCase):
    def test_file_in_scope_passes(self):
        d = make_project({"src/contracts/a.py": "x = 1\n"}, ["scope/**", "src/contracts/**"])
        self.assertEqual(cs.run(d, "G0"), [])

    def test_stray_file_is_flagged(self):
        d = make_project({"src/fd_connect/b.py": "x = 1\n"}, ["scope/**"])
        self.assertTrue(any("[S1]" in p for p in cs.run(d, "G0")))

    def test_cross_fd_import_is_flagged(self):
        d = make_project({"src/fd_signal/s.py": "from fd_connect import cam\n"}, ["scope/**", "src/**"])
        self.assertTrue(any("[S2]" in p for p in cs.run(d, "G0")))

    def test_flow_may_import_fd(self):
        d = make_project({"src/fd_flow/f.py": "from fd_connect import cam\n"}, ["scope/**", "src/**"])
        self.assertEqual(cs.run(d, "G0"), [])

    def test_missing_scope_is_reported(self):
        d = Path(tempfile.mkdtemp())
        self.assertTrue(any("[S3]" in p for p in cs.run(d, "G9")))


if __name__ == "__main__":
    unittest.main()
