import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from contracts.logic import ErrorEvent
from fd_config.config_loader import ConfigLoader, SystemConfig
from fd_diagnose import AutoReporter, DiagnosisFinding, ErrorRecorder, EvaluationReport
from fd_eval import EvalThresholds, MetricsCollector
from fd_faultsim import FaultInjector, FaultScenario

FAULTS_DIR = ROOT / "workbench" / "faults"


class AutoReporterTest(unittest.TestCase):
    def test_node_contract_and_self_test(self):
        reporter = AutoReporter()
        self.assertEqual(reporter.node_id, "diagnose")
        self.assertEqual(reporter.health(), "OK")
        ok, msg = reporter.self_test()
        self.assertTrue(ok, msg)
        self.assertIn("AutoReporter sẵn sàng", msg)

    def test_full_g5_pipeline_evaluation_report_pass(self):
        loader = ConfigLoader()
        cfg = loader.load(trace_id="test-g5-report")
        self.assertIsInstance(cfg, SystemConfig)
        assert isinstance(cfg, SystemConfig)
        assert cfg.eval is not None

        thresholds = EvalThresholds(
            min_throughput_fps=cfg.eval.min_throughput_fps,
            max_p95_latency_ms=cfg.eval.max_p95_latency_ms,
            max_p99_latency_ms=cfg.eval.max_p99_latency_ms,
            max_queue_depth=cfg.eval.max_queue_depth,
            max_ram_growth_mb=cfg.eval.max_ram_growth_mb,
        )
        collector = MetricsCollector(thresholds=thresholds, reservoir_size=256)
        for i in range(500):
            collector.record_hop("connect.camera", latency_ms=0.08, ts_ms=1000 + i, queue_depth=2)
            collector.record_hop("signal", latency_ms=0.11, ts_ms=1000 + i, queue_depth=3)
            collector.record_hop("process.sampler", latency_ms=0.05, ts_ms=1000 + i, queue_depth=1)
        metrics_summary = collector.summarize(elapsed_override_ms=100.0)
        self.assertTrue(metrics_summary.passed_thresholds, f"Violations: {metrics_summary.violations}")

        injector = FaultInjector()
        fault_reports = []
        for fname in (
            "network_drop.yaml",
            "uart_degraded.yaml",
            "camera_fps_drop.yaml",
            "edge_contract_violation.yaml",
        ):
            sc = injector.load_scenario(FAULTS_DIR / fname)
            assert isinstance(sc, FaultScenario)
            fault_reports.append(injector.run_scenario(sc))

        reporter = AutoReporter()
        report = reporter.generate_report(
            metrics_summary=metrics_summary,
            fault_reports=fault_reports,
            error_events=(),
            gate="G5",
            timestamp_iso="2026-10-26T15:42:00+07:00",
        )
        self.assertIsInstance(report, EvaluationReport)
        self.assertEqual(report.overall_verdict, "PASS")
        self.assertTrue(report.metrics_passed)
        self.assertTrue(report.faultsim_passed)
        self.assertEqual(len(report.scenario_summaries), 4)
        self.assertIn("# BENCHMARK-G5", report.markdown_text)
        self.assertIn("FI-051-NET", report.markdown_text)
        self.assertIn("FI-051-UART", report.markdown_text)

        parsed_json = json.loads(report.json_text)
        self.assertEqual(parsed_json["verdict"], "PASS")
        self.assertEqual(len(parsed_json["fault_scenarios"]), 4)
        self.assertIn("connect.camera", parsed_json["metrics"]["stages"])

    def test_failure_detection_and_bug_template_draft_generation(self):
        thresholds = EvalThresholds(min_throughput_fps=5000.0, max_p95_latency_ms=0.1)
        collector = MetricsCollector(thresholds=thresholds)
        for i in range(50):
            collector.record_hop("process.ai", latency_ms=2.5, ts_ms=2000 + i, queue_depth=10)
        bad_metrics = collector.summarize(elapsed_override_ms=1000.0)
        self.assertFalse(bad_metrics.passed_thresholds)

        recorder = ErrorRecorder()
        fatal_ev = ErrorEvent(
            trace_id="tr-fatal-01",
            ts_ms=3000,
            source="flow",
            code="E_FLOW_EDGE_TYPE",
            node_path="process.ai->process.rules",
            err_class="contract",
            message="Sai kiểu gói trên cạnh process.ai->process.rules",
            recoverable=False,
        )
        recorder.record(fatal_ev)

        reporter = AutoReporter()
        report = reporter.generate_report(
            metrics_summary=bad_metrics,
            fault_reports=(),
            error_events=recorder.history(),
            gate="G5",
        )
        self.assertEqual(report.overall_verdict, "FAIL")
        self.assertFalse(report.metrics_passed)
        self.assertEqual(len(report.findings), 1)
        finding = report.findings[0]
        self.assertIsInstance(finding, DiagnosisFinding)
        self.assertEqual(finding.code, "E_FLOW_EDGE_TYPE")
        self.assertEqual(finding.node_path, "process.ai->process.rules")
        self.assertEqual(finding.err_class_vn, "hợp đồng")
        self.assertIn("## Triệu chứng", finding.bug_draft_markdown)
        self.assertIn("## nodePath và ErrorCode", finding.bug_draft_markdown)
        self.assertIn("## Cách tái hiện ở workbench", finding.bug_draft_markdown)
        self.assertIn("## Nguyên nhân gốc", finding.bug_draft_markdown)
        self.assertIn("## Thay đổi nhỏ nhất đề xuất (F3)", finding.bug_draft_markdown)
        self.assertIn("## Test phải chạy lại (F5)", finding.bug_draft_markdown)

    def test_write_report_to_files_and_missing_path_check(self):
        reporter = AutoReporter()
        err_none = reporter.write_report(
            report=reporter.generate_report(MetricsCollector().summarize(10.0)),
            markdown_path=None,
            json_path=None,
        )
        self.assertIsInstance(err_none, ErrorEvent)
        assert isinstance(err_none, ErrorEvent)
        self.assertEqual(err_none.code, "E_CFG_INVALID")

        collector = MetricsCollector(thresholds=EvalThresholds(min_throughput_fps=10.0))
        collector.record_hop("signal", latency_ms=0.1, ts_ms=1000, queue_depth=1)
        rep = reporter.generate_report(collector.summarize(elapsed_override_ms=10.0))

        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "BENCHMARK-G5.md"
            js_path = Path(tmpdir) / "BENCHMARK-G5.json"
            err = reporter.write_report(rep, markdown_path=md_path, json_path=js_path)
            self.assertIsNone(err)
            self.assertTrue(md_path.exists())
            self.assertTrue(js_path.exists())
            self.assertIn("BENCHMARK-G5", md_path.read_text(encoding="utf-8"))
            loaded = json.loads(js_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
