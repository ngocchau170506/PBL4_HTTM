import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from contracts.logic import ErrorEvent, RawPacket, SignalPacket
from fd_config.config_loader import ConfigLoader, EvalConfig, SystemConfig
from fd_eval import EvalThresholds, MetricsCollector, MetricsSummary


class MetricsCollectorTest(unittest.TestCase):
    def test_node_contract_and_self_test(self):
        collector = MetricsCollector()
        self.assertEqual(collector.node_id, "eval")
        self.assertEqual(collector.health(), "OK")
        ok, msg = collector.self_test()
        self.assertTrue(ok, msg)
        self.assertIn("MetricsCollector sẵn sàng", msg)

    def test_config_loader_parses_eval_section(self):
        loader = ConfigLoader()
        cfg = loader.load(trace_id="test-eval-cfg")
        self.assertIsInstance(cfg, SystemConfig)
        assert isinstance(cfg, SystemConfig)
        self.assertIsNotNone(cfg.eval)
        assert isinstance(cfg.eval, EvalConfig)
        self.assertEqual(cfg.eval.min_throughput_fps, 3000.0)
        self.assertEqual(cfg.eval.max_p95_latency_ms, 1.0)
        self.assertEqual(cfg.eval.max_p99_latency_ms, 5.0)
        self.assertEqual(cfg.eval.max_queue_depth, 100)
        self.assertEqual(cfg.eval.max_ram_growth_mb, 5.0)
        self.assertEqual(cfg.eval.sustained_test_duration_s, 30)

    def test_config_loader_rejects_invalid_eval_section(self):
        loader = ConfigLoader()
        base = loader.load(trace_id="test-eval-base")
        self.assertIsInstance(base, SystemConfig)

        raw_bad_p99 = {
            "mode": "sim",
            "camera": {"source": "webcam", "index": 0, "uri": None, "fps": 25, "width": 640, "height": 480},
            "esp32": {"enabled": False, "uri": "COM3"},
            "sampler": {"target_fps": 5},
            "baseline": {"seconds_min": 5, "seconds_max": 10},
            "ai": {"adapter": "stub", "timeout_ms": 500},
            "response": {"pre_event_ms": 2000, "buffer_frames": 50},
            "rules": {
                "min_conf": 0.5,
                "max_gap_ms": 600,
                "eye_drowsiness": {"trigger_ms": 1500, "closed_labels": ["closed"], "open_labels": ["open"]},
                "mouth_yawn": {"trigger_ms": 2500, "yawn_labels": ["yawn"], "no_yawn_labels": ["noYawn"]},
            },
            "eval": {
                "min_throughput_fps": 3000,
                "max_p95_latency_ms": 5.0,
                "max_p99_latency_ms": 2.0,
                "max_queue_depth": 100,
                "max_ram_growth_mb": 5.0,
                "sustained_test_duration_s": 30,
            },
        }
        res = loader.validate_dict(raw_bad_p99, trace_id="test-eval-bad-p99")
        self.assertIsInstance(res, ErrorEvent)
        assert isinstance(res, ErrorEvent)
        self.assertEqual(res.code, "E_CFG_INVALID")
        self.assertIn("eval.max_p99_latency_ms", res.message)

    def test_percentiles_and_multi_stage_summary(self):
        thresholds = EvalThresholds(
            min_throughput_fps=100.0,
            max_p95_latency_ms=1.0,
            max_p99_latency_ms=2.0,
            max_queue_depth=50,
            max_ram_growth_mb=10.0,
        )
        collector = MetricsCollector(thresholds=thresholds, reservoir_size=256)

        for i in range(1, 101):
            lat = i * 0.005
            err1 = collector.record_hop("signal", latency_ms=lat, ts_ms=1000 + i, queue_depth=5)
            self.assertIsNone(err1)
            err2 = collector.record_hop("process.sampler", latency_ms=lat * 0.5, ts_ms=1000 + i, queue_depth=3)
            self.assertIsNone(err2)

        summary = collector.summarize(elapsed_override_ms=100.0)
        self.assertIsInstance(summary, MetricsSummary)
        self.assertEqual(summary.total_packets, 200)
        self.assertAlmostEqual(summary.overall_fps, 2000.0, places=2)
        self.assertEqual(summary.peak_queue_depth, 5)
        self.assertIn("signal", summary.stages)
        self.assertIn("process.sampler", summary.stages)
        self.assertEqual(summary.stages["signal"].count, 100)
        self.assertAlmostEqual(summary.stages["signal"].min_latency_ms, 0.005, places=5)
        self.assertAlmostEqual(summary.stages["signal"].max_latency_ms, 0.500, places=5)
        self.assertAlmostEqual(summary.stages["signal"].p50_latency_ms, 0.2525, places=3)
        self.assertAlmostEqual(summary.stages["signal"].p95_latency_ms, 0.47525, places=3)
        self.assertTrue(summary.passed_thresholds, f"Violations: {summary.violations}")

    def test_record_packet_and_error_events(self):
        thresholds = EvalThresholds(max_queue_depth=10)
        collector = MetricsCollector(thresholds=thresholds)

        pkt = SignalPacket(
            trace_id="tr-001",
            ts_ms=2000,
            source="signal",
            frame_id=1,
            width=640,
            height=480,
        )
        self.assertIsNone(collector.record_packet(pkt, latency_ms=0.15, queue_depth=4))

        bad_pkt = RawPacket(
            trace_id="",
            ts_ms=2005,
            source="connect.camera",
            payload=b"frame",
            sensor_kind="camera",
        )
        err_trace = collector.record_packet(bad_pkt, latency_ms=0.1)
        self.assertIsInstance(err_trace, ErrorEvent)
        assert isinstance(err_trace, ErrorEvent)
        self.assertEqual(err_trace.code, "E_SIG_BAD_FORMAT")
        self.assertEqual(collector.health(), "DEGRADED")

        err_queue = collector.record_hop("response", latency_ms=0.2, ts_ms=2010, queue_depth=25)
        self.assertIsInstance(err_queue, ErrorEvent)
        assert isinstance(err_queue, ErrorEvent)
        self.assertEqual(err_queue.code, "E_FLOW_QUEUE_FULL")
        self.assertEqual(err_queue.node_path, "eval.response")

        summary = collector.summarize(elapsed_override_ms=50.0)
        self.assertEqual(summary.error_count, 1)
        self.assertEqual(summary.error_by_code.get("E_FLOW_QUEUE_FULL"), 1)
        self.assertFalse(summary.passed_thresholds)
        self.assertTrue(any("peak_queue_depth" in v for v in summary.violations))

    def test_sustained_50k_packets_bounded_memory_and_release(self):
        collector = MetricsCollector(reservoir_size=512)
        collector.reset()

        for i in range(50000):
            err = collector.record_hop(
                stage="connect.camera" if (i & 1) == 0 else "signal",
                latency_ms=0.12 + (i % 20) * 0.01,
                ts_ms=10000 + (i // 5),
                queue_depth=i % 15,
            )
            self.assertIsNone(err)

        collector.release_memory()
        summary = collector.summarize(elapsed_override_ms=10000.0)
        self.assertEqual(summary.total_packets, 50000)
        self.assertAlmostEqual(summary.overall_fps, 5000.0, places=1)
        self.assertLess(summary.ram_growth_mb, 1.0)
        self.assertTrue(summary.passed_thresholds, f"Violations: {summary.violations}")

        collector.reset()
        after_reset = collector.summarize(elapsed_override_ms=100.0)
        self.assertEqual(after_reset.total_packets, 0)
        self.assertEqual(collector.health(), "OK")


if __name__ == "__main__":
    unittest.main()
