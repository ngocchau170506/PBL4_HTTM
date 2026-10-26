import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from contracts.logic import ErrorEvent, ViolationEvent
from fd_eval import MetricsCollector
from fd_faultsim import FaultExecutionReport, FaultInjector, FaultScenario
from fd_flow.flow_engine import FlowEngine
from fd_response.server_dispatcher import ServerDispatcher

FAULTS_DIR = ROOT / "workbench" / "faults"


class FaultSimTest(unittest.TestCase):
    def test_node_contract_and_self_test(self):
        injector = FaultInjector()
        self.assertEqual(injector.node_id, "faultsim")
        self.assertEqual(injector.health(), "OK")
        ok, msg = injector.self_test()
        self.assertTrue(ok, msg)
        self.assertIn("FaultInjector sẵn sàng", msg)

    def test_load_and_run_all_four_g5_yaml_scenarios(self):
        injector = FaultInjector()
        yaml_files = [
            ("network_drop.yaml", "FI-051-NET", "response.dispatcher", True),
            ("uart_degraded.yaml", "FI-051-UART", "connect.esp32", True),
            ("camera_fps_drop.yaml", "FI-051-FPS", "signal", False),
            ("edge_contract_violation.yaml", "FI-051-EDGE", "process.ai->process.rules", True),
        ]
        for filename, expected_id, expected_path, expect_100_cam in yaml_files:
            with self.subTest(file=filename):
                sc = injector.load_scenario(FAULTS_DIR / filename)
                self.assertIsInstance(sc, FaultScenario, f"Failed loading {filename}: {sc}")
                assert isinstance(sc, FaultScenario)
                self.assertEqual(sc.scenario_id, expected_id)
                self.assertEqual(sc.target_node_path, expected_path)

                report = injector.run_scenario(sc)
                self.assertIsInstance(report, FaultExecutionReport)
                self.assertTrue(report.passed, report.detail)
                self.assertTrue(report.matched_expected_codes, report.detail)
                self.assertTrue(report.matched_node_paths, report.detail)
                self.assertTrue(report.recovered_after_fault, report.detail)
                if expect_100_cam:
                    self.assertEqual(report.camera_survival_rate, 1.0)
                else:
                    self.assertLess(report.camera_survival_rate, 1.0)

    def test_load_legacy_scope_violation_yaml(self):
        injector = FaultInjector()
        sc = injector.load_scenario(FAULTS_DIR / "get.yaml")
        self.assertIsInstance(sc, FaultScenario)
        assert isinstance(sc, FaultScenario)
        self.assertEqual(sc.scenario_id, "FI-001")
        self.assertIn("E_SCOPE_VIOLATION", sc.expected_codes)
        report = injector.run_scenario(sc)
        self.assertTrue(report.passed, report.detail)

    def test_invalid_scenario_returns_error_event(self):
        injector = FaultInjector()
        err_missing = injector.load_scenario(FAULTS_DIR / "nonexistent_fault.yaml")
        self.assertIsInstance(err_missing, ErrorEvent)
        assert isinstance(err_missing, ErrorEvent)
        self.assertEqual(err_missing.code, "E_CFG_INVALID")
        self.assertEqual(injector.health(), "DEGRADED")

        err_bad_code = injector.validate_scenario_dict(
            {
                "id": "BAD-01",
                "loai": "custom",
                "ma_loi_ky_vong": ["E_NOT_IN_CONTRACT"],
            }
        )
        self.assertIsInstance(err_bad_code, ErrorEvent)
        assert isinstance(err_bad_code, ErrorEvent)
        self.assertEqual(err_bad_code.code, "E_CFG_INVALID")

    def test_network_drop_integration_with_server_dispatcher_and_metrics(self):
        injector = FaultInjector()
        sc = injector.load_scenario(FAULTS_DIR / "network_drop.yaml")
        assert isinstance(sc, FaultScenario)

        transport = injector.make_network_transport(sc)
        dispatcher = ServerDispatcher(
            transport=transport,
            max_outbox=2,
            max_spool=sc.spool_capacity,
            failure_threshold=sc.failure_threshold,
            timeout_ms=200,
        )
        collector = MetricsCollector()

        emitted_codes = set()
        for idx in range(sc.total_steps):
            ev = ViolationEvent(
                trace_id=f"tr-net-{idx}",
                ts_ms=10000 + idx * 100,
                source="process.rules",
                rule="eye_drowsiness",
                t0_ms=9500 + idx * 100,
                pre_ms=2000,
                frame_ids=(idx * 2, idx * 2 + 1),
            )
            enq_err = dispatcher.enqueue(ev)
            if isinstance(enq_err, ErrorEvent):
                emitted_codes.add(enq_err.code)
                collector.record_error(enq_err)

            if not dispatcher.circuit_open or idx % 2 == 0:
                res = dispatcher.flush_one()
                if isinstance(res, ErrorEvent):
                    emitted_codes.add(res.code)
                    collector.record_error(res)
            collector.record_hop(
                "response",
                latency_ms=0.15,
                ts_ms=ev.ts_ms,
                queue_depth=dispatcher.spool_size,
            )

        self.assertIn("E_RESP_SEND", emitted_codes)
        self.assertIn("E_RESP_NO_ACK", emitted_codes)
        self.assertIn("E_RESP_BUFFER_OVERRUN", emitted_codes)
        summary = collector.summarize(elapsed_override_ms=10.0)
        self.assertEqual(summary.total_packets, sc.total_steps)
        self.assertGreater(summary.error_count, 0)

    def test_edge_violation_integration_with_flow_engine(self):
        injector = FaultInjector()
        sc = injector.load_scenario(FAULTS_DIR / "edge_contract_violation.yaml")
        assert isinstance(sc, FaultScenario)

        engine = FlowEngine()
        engine.boot()

        wrong_pkt, sim_err = injector.inject_edge_step(sc.fault_start_step, sc)
        self.assertIsNotNone(sim_err)
        assert isinstance(sim_err, ErrorEvent)
        self.assertEqual(sim_err.code, "E_FLOW_EDGE_TYPE")
        self.assertEqual(sim_err.node_path, "process.ai->process.rules")

        flow_err = engine.send_across_edge("process.ai", "process.rules", wrong_pkt)
        self.assertIsInstance(flow_err, ErrorEvent)
        assert isinstance(flow_err, ErrorEvent)
        self.assertEqual(flow_err.code, "E_FLOW_EDGE_TYPE")
        self.assertEqual(flow_err.node_path, "process.ai->process.rules")


if __name__ == "__main__":
    unittest.main()
