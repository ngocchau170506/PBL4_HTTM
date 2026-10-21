import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_process.ai.adapter_base import run_ai
from fd_process.ai.stub_adapter import StubAdapter


class StubAdapterTest(unittest.TestCase):
    def test_valid_infer_via_run_ai(self):
        stub = StubAdapter()
        stub.load()
        sig = L.SignalPacket(
            trace_id="ai-t-01",
            ts_ms=2000,
            source="signal",
            frame_id=5,
            width=640,
            height=480,
            driver_id="drv-01",
        )
        res = run_ai(stub, sig, timeout_ms=500)
        self.assertIsInstance(res, L.DetectionResult)
        self.assertEqual(res.trace_id, "ai-t-01")
        self.assertEqual(res.frame_id, 5)
        self.assertEqual(len(res.items), 1)
        self.assertEqual(res.items[0].label, "driver_normal")

    def test_missing_trace_id_emits_e_sig_bad_format(self):
        stub = StubAdapter()
        sig = L.SignalPacket(
            trace_id="",
            ts_ms=2000,
            source="signal",
            frame_id=1,
            width=640,
            height=480,
        )
        evt = run_ai(stub, sig, timeout_ms=500)
        self.assertIsInstance(evt, L.ErrorEvent)
        self.assertEqual(evt.code, "E_SIG_BAD_FORMAT")
        self.assertEqual(evt.node_path, "process.sampler->process.ai(stub)")

    def test_down_adapter_emits_e_flow_node_down(self):
        stub = StubAdapter()
        stub.set_health("DOWN")
        sig = L.SignalPacket(
            trace_id="ai-t-down",
            ts_ms=2000,
            source="signal",
            frame_id=2,
            width=640,
            height=480,
        )
        evt = run_ai(stub, sig, timeout_ms=500)
        self.assertIsInstance(evt, L.ErrorEvent)
        self.assertEqual(evt.code, "E_FLOW_NODE_DOWN")
        self.assertEqual(evt.node_path, "process.sampler->process.ai(stub)")

    def test_timeout_emits_e_proc_timeout(self):
        stub = StubAdapter(simulated_delay_ms=40)
        sig = L.SignalPacket(
            trace_id="ai-t-slow",
            ts_ms=2000,
            source="signal",
            frame_id=3,
            width=640,
            height=480,
        )
        evt = run_ai(stub, sig, timeout_ms=10)
        self.assertIsInstance(evt, L.ErrorEvent)
        self.assertEqual(evt.code, "E_PROC_TIMEOUT")
        self.assertEqual(evt.node_path, "process.sampler->process.ai(stub)")

    def test_invalid_confidence_emits_e_proc_bad_output(self):
        stub = StubAdapter(
            default_items=(L.Detection(label="bad_conf", conf=1.5),)
        )
        sig = L.SignalPacket(
            trace_id="ai-t-conf",
            ts_ms=2000,
            source="signal",
            frame_id=4,
            width=640,
            height=480,
        )
        evt = run_ai(stub, sig, timeout_ms=500)
        self.assertIsInstance(evt, L.ErrorEvent)
        self.assertEqual(evt.code, "E_PROC_BAD_OUTPUT")

    def test_self_test(self):
        stub = StubAdapter()
        ok, detail = stub.self_test()
        self.assertTrue(ok)
        self.assertIn("StubAdapter OK", detail)


if __name__ == "__main__":
    unittest.main()
