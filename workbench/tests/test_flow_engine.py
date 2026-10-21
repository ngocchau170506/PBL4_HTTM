import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_diagnose.error_recorder import ErrorRecorder
from fd_flow.flow_engine import FlowEngine
from fd_response.local_alert import ResponseLogger


class FlowEngineTest(unittest.TestCase):
    def test_full_g1_pipeline_step_once(self):
        engine = FlowEngine()
        self.assertIsNone(engine.boot(trace_id="t-boot-01"))
        self.assertEqual(engine.breaker.system_state, "MONITOR")

        res = engine.step_once(trace_id="t-step-01", driver_id="drv-01")
        self.assertIsInstance(res, L.DetectionResult)
        self.assertEqual(res.trace_id, "t-step-01")
        self.assertEqual(res.frame_id, 1)
        self.assertEqual(engine.response.processed_count, 1)
        self.assertIn("trace_id=t-step-01", engine.response.latest_log() or "")

    def test_wrong_packet_type_on_edge_emits_e_flow_edge_type(self):
        engine = FlowEngine()
        engine.boot(trace_id="t-boot-02")

        wrong_pkt = L.RawPacket(
            trace_id="t-edge-bad",
            ts_ms=1000,
            source="connect.camera",
            payload=b"bad",
            sensor_kind="camera",
        )
        err = engine.send_across_edge(
            "signal", "process.sampler", wrong_pkt, trace_id="t-edge-bad"
        )
        self.assertIsInstance(err, L.ErrorEvent)
        self.assertEqual(err.code, "E_FLOW_EDGE_TYPE")
        self.assertEqual(err.node_path, "signal->process.sampler")
        self.assertEqual(engine.diagnose.latest(), err)

    def test_queue_overflow_emits_e_flow_queue_full(self):
        engine = FlowEngine(max_queue_depth=2)
        engine.boot(trace_id="t-boot-03")
        raw = L.RawPacket(
            trace_id="t-q-01",
            ts_ms=1000,
            source="connect.camera",
            payload=b"1234",
            sensor_kind="camera",
        )
        self.assertIsNone(engine.send_across_edge("connect.camera", "signal", raw, "t-q-01"))
        self.assertIsNone(engine.send_across_edge("connect.camera", "signal", raw, "t-q-02"))
        overflow_err = engine.send_across_edge("connect.camera", "signal", raw, "t-q-03")
        self.assertIsInstance(overflow_err, L.ErrorEvent)
        self.assertEqual(overflow_err.code, "E_FLOW_QUEUE_FULL")
        self.assertEqual(overflow_err.node_path, "connect.camera->signal")

    def test_camera_disconnect_emits_e_conn_lost_and_diagnose_records_it(self):
        engine = FlowEngine()
        engine.boot(trace_id="t-boot-04")
        assert engine.camera is not None
        engine.camera.simulate_disconnect()

        res = engine.step_once(trace_id="t-cam-lost")
        self.assertIsInstance(res, L.ErrorEvent)
        self.assertEqual(res.code, "E_CONN_LOST")
        self.assertEqual(res.node_path, "connect.camera")
        self.assertEqual(engine.breaker.system_state, "FAULT")
        self.assertEqual(engine.health(), "DOWN")

        diag_last = engine.diagnose.latest()
        self.assertIsNotNone(diag_last)
        assert diag_last is not None
        self.assertEqual(diag_last.code, "E_CONN_LOST")
        self.assertEqual(diag_last.node_path, "connect.camera")
        draft = engine.diagnose.format_bug_draft("BUG-001", diag_last)
        self.assertIn("connect.camera", draft)

    def test_optional_esp32_error_enters_degraded_without_stopping_camera_flow(self):
        engine = FlowEngine()
        engine.boot(trace_id="t-boot-05")
        esp_err = L.ErrorEvent(
            trace_id="t-esp-lost",
            ts_ms=1500,
            source="connect.esp32",
            code="E_CONN_LOST",
            node_path="connect.esp32",
            err_class="source",
            message="ESP32 mất kết nối tạm thời",
            recoverable=True,
        )
        engine.diagnose.record(esp_err)
        new_state = engine.breaker.on_error(esp_err)
        self.assertEqual(new_state, "DEGRADED")

        cam_res = engine.step_once(trace_id="t-cam-still-runs")
        self.assertIsInstance(cam_res, L.DetectionResult)

    def test_gate_g1_bounded_memory_under_continuous_frames(self):
        resp = ResponseLogger(max_logs=50)
        diag = ErrorRecorder(max_events=200)
        engine = FlowEngine(response_logger=resp, diagnose_recorder=diag, max_queue_depth=16)
        engine.boot(trace_id="t-boot-stress")

        total_frames = 3000
        for i in range(1, total_frames + 1):
            res = engine.step_once(trace_id=f"t-stress-{i}")
            self.assertIsInstance(res, L.DetectionResult)

        self.assertEqual(resp.processed_count, total_frames)
        self.assertLessEqual(resp.buffer_size, 50)
        self.assertEqual(engine.queues.depth("connect.camera", "signal"), 0)
        self.assertEqual(engine.queues.depth("signal", "process.sampler"), 0)
        self.assertEqual(engine.queues.depth("process.sampler", "process.ai"), 0)
        self.assertEqual(engine.queues.depth("process.ai", "process.rules"), 0)

    def test_g2_sampled_pipeline_with_baseline_and_driver_switch(self):
        engine = FlowEngine()
        self.assertIsNone(engine.boot(trace_id="t-boot-g2"))
        assert engine.sampler is not None
        assert engine.baseline is not None

        passed_drv1 = 0
        for i in range(150):
            ts_ms = 1000 + i * 40
            res = engine.step_sampled(
                trace_id=f"t-g2-d1-{i + 1}",
                driver_id="driver-01",
                ts_override_ms=ts_ms,
            )
            if res is not None:
                self.assertIsInstance(res, L.DetectionResult)
                passed_drv1 += 1

        self.assertEqual(passed_drv1, 30)
        prof1 = engine.baseline.current_profile("driver-01")
        self.assertIsNotNone(prof1)
        assert prof1 is not None
        self.assertEqual(prof1.state, "LOCKED")
        self.assertEqual(prof1.duration_ms, 5000)
        self.assertEqual(prof1.frame_count, 26)
        self.assertEqual(engine.breaker.system_state, "MONITOR")

        res_d2_first = engine.step_sampled(
            trace_id="t-g2-d2-1",
            driver_id="driver-02",
            ts_override_ms=10000,
        )
        self.assertIsInstance(res_d2_first, L.DetectionResult)
        self.assertEqual(engine.breaker.system_state, "BASELINE")
        prof2_collecting = engine.baseline.current_profile("driver-02")
        self.assertIsNotNone(prof2_collecting)
        assert prof2_collecting is not None
        self.assertEqual(prof2_collecting.state, "COLLECTING")
        self.assertEqual(engine.baseline.current_profile("driver-01"), prof1)

        for j in range(1, 130):
            ts_ms = 10000 + j * 40
            engine.step_sampled(
                trace_id=f"t-g2-d2-{j + 1}",
                driver_id="driver-02",
                ts_override_ms=ts_ms,
            )

        prof2_locked = engine.baseline.current_profile("driver-02")
        self.assertIsNotNone(prof2_locked)
        assert prof2_locked is not None
        self.assertEqual(prof2_locked.state, "LOCKED")
        self.assertEqual(engine.breaker.system_state, "MONITOR")
        self.assertEqual(engine.baseline.current_profile("driver-01"), prof1)
        self.assertEqual(engine.queues.depth("process.sampler", "process.baseline"), 0)


if __name__ == "__main__":
    unittest.main()
