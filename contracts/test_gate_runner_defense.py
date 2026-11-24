"""Mandatory negative tests proving Gate Runner cannot emit False PASS (F-001, F-002, F-003, F-010)."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from contracts.gate_runner import (
    FaultCheckInput,
    compute_source_tree_hash,
    evaluate_evidence_fail_closed,
    evaluate_fault_report,
)


class TestGateRunnerFailClosed(unittest.TestCase):
    def test_f001_none_metrics_and_empty_faults_must_not_pass(self):
        ev = evaluate_evidence_fail_closed(
            metrics_summary=None,
            fault_reports=(),
            test_counts={"total": 5, "failed": 0},
            source_hash="abc123",
        )
        self.assertEqual(ev.overall_verdict, "BLOCKED")
        self.assertEqual(ev.health, "DEGRADED")

    def test_f002_unrecovered_fault_must_fail(self):
        fr = FaultCheckInput(
            scenario_id="NET_DROP_NO_RECOVERY",
            requires_recovery=True,
            recovered_after_fault=False,
            recovery_time_ms=None,
            recovery_deadline_ms=5000.0,
            matched_expected_codes=True,
            observed_on_real_sut=True,
        )
        verdict, _ = evaluate_fault_report(fr)
        self.assertEqual(verdict, "FAIL")

        ev = evaluate_evidence_fail_closed(
            metrics_summary={"passed_thresholds": True, "total_packets": 100},
            fault_reports=(fr,),
            test_counts={"total": 10, "failed": 0},
            source_hash="abc123",
        )
        self.assertEqual(ev.overall_verdict, "FAIL")
        self.assertEqual(ev.health, "DOWN")

    def test_f003_failed_metrics_cannot_have_ok_health(self):
        fr = FaultCheckInput(
            scenario_id="OK_SCENARIO",
            requires_recovery=True,
            recovered_after_fault=True,
            recovery_time_ms=500.0,
            recovery_deadline_ms=5000.0,
            matched_expected_codes=True,
            observed_on_real_sut=True,
        )
        ev = evaluate_evidence_fail_closed(
            metrics_summary={"passed_thresholds": False, "total_packets": 100},
            fault_reports=(fr,),
            test_counts={"total": 10, "failed": 0},
            source_hash="abc123",
        )
        self.assertEqual(ev.overall_verdict, "FAIL")
        self.assertNotEqual(ev.health, "OK")
        self.assertEqual(ev.health, "DOWN")

    def test_f010_zero_tests_must_be_blocked(self):
        fr = FaultCheckInput(
            scenario_id="OK_SCENARIO",
            requires_recovery=True,
            recovered_after_fault=True,
            recovery_time_ms=200.0,
            recovery_deadline_ms=2000.0,
            matched_expected_codes=True,
            observed_on_real_sut=True,
        )
        ev = evaluate_evidence_fail_closed(
            metrics_summary={"passed_thresholds": True, "total_packets": 50},
            fault_reports=(fr,),
            test_counts={"total": 0, "failed": 0},
            source_hash="abc123",
        )
        self.assertEqual(ev.overall_verdict, "BLOCKED")
        self.assertNotEqual(ev.overall_verdict, "PASS")

    def test_source_hash_drift_invalidates_prior_pass(self):
        ev = evaluate_evidence_fail_closed(
            metrics_summary={"passed_thresholds": True, "total_packets": 50},
            fault_reports=(),
            test_counts={"total": 10, "failed": 0},
            source_hash="new_hash",
            expected_source_hash="old_hash",
            require_fault_reports=False,
        )
        self.assertEqual(ev.overall_verdict, "INVALIDATED")

    def test_valid_full_evidence_passes_with_ok_health(self):
        fr = FaultCheckInput(
            scenario_id="REAL_RECOVERY",
            requires_recovery=True,
            recovered_after_fault=True,
            recovery_time_ms=350.0,
            recovery_deadline_ms=3000.0,
            matched_expected_codes=True,
            observed_on_real_sut=True,
        )
        ev = evaluate_evidence_fail_closed(
            metrics_summary={"passed_thresholds": True, "total_packets": 120},
            fault_reports=(fr,),
            test_counts={"total": 25, "failed": 0},
            source_hash="abc123",
            expected_source_hash="abc123",
        )
        self.assertEqual(ev.overall_verdict, "PASS")
        self.assertEqual(ev.health, "OK")

    def test_f01_source_hash_includes_pi_edge_evidence_and_skips_server_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pi_edge" / "evidence").mkdir(parents=True)
            (root / "server_ai" / "evidence").mkdir(parents=True)
            sp = root / "pi_edge" / "evidence" / "spool_store.py"
            sp.write_text("v = 1\n", encoding="utf-8")
            ev_img = root / "server_ai" / "evidence" / "dump.json"
            ev_img.write_text('{"a": 1}\n', encoding="utf-8")

            h1 = compute_source_tree_hash(root)
            # Mutating server_ai/evidence runtime files must NOT change source hash
            ev_img.write_text('{"a": 2}\n', encoding="utf-8")
            self.assertEqual(compute_source_tree_hash(root), h1)
            # Adding or editing CLAUDE.md behavioral notes must NOT invalidate stage locks
            (root / "CLAUDE.md").write_text("# rules v1\n", encoding="utf-8")
            self.assertEqual(compute_source_tree_hash(root), h1)
            # Mutating pi_edge/evidence/*.py MUST invalidate source hash (fixes F-01!)
            sp.write_text("v = 2\n", encoding="utf-8")
            self.assertNotEqual(compute_source_tree_hash(root), h1)

            # Mutating udev .rules file MUST also invalidate source hash!
            (root / "pi_edge" / "deploy").mkdir(parents=True)
            udev = root / "pi_edge" / "deploy" / "99-pi-ai-esp32.rules"
            udev.write_text('KERNELS=="1-1.3"\n', encoding="utf-8")
            h2 = compute_source_tree_hash(root)
            udev.write_text('KERNELS=="1-1.4"\n', encoding="utf-8")
            self.assertNotEqual(compute_source_tree_hash(root), h2)

    def test_skipped_unittest_does_not_count_as_executed(self):
        from contracts.gate_runner import _parse_unittest_count

        self.assertEqual(_parse_unittest_count("Ran 12 tests in 0.1s\n\nOK (skipped=12)"), 0)
        self.assertEqual(_parse_unittest_count("Ran 12 tests in 0.1s\n\nOK (skipped=2)"), 10)
        self.assertEqual(_parse_unittest_count("Ran 7 tests in 0.2s\n\nOK"), 7)


if __name__ == "__main__":
    unittest.main()
