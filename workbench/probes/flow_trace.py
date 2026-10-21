#!/usr/bin/env python3
"""Trạm thử nghiệm độc lập cho fd_flow & fd_diagnose (workbench/probes/flow_trace.py).

- Kiểm tra chạy luồng G1 (connect -> signal -> process(stub) -> response).
- Bơm gói sai kiểu giữa 2 node để xác minh fd_flow báo đúng E_FLOW_EDGE_TYPE và chỉ đúng cạnh.
- Ngắt camera giả để xác minh báo E_CONN_LOST đúng node_path='connect.camera' và ghi vào fd_diagnose.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from contracts.logic import DetectionResult, ErrorEvent, RawPacket
from fd_flow.flow_engine import FlowEngine


def run_flow_probe() -> int:
    engine = FlowEngine()
    boot_err = engine.boot(trace_id="flow-probe-boot")
    if boot_err is not None:
        print(f"[FAIL] Khởi động FlowEngine lỗi: {boot_err.code}")
        return 1

    step_res = engine.step_once(trace_id="flow-probe-step-01", driver_id="drv-01")
    if not isinstance(step_res, DetectionResult):
        print(f"[FAIL] Bước chạy luồng chuẩn thất bại: {step_res}")
        return 1

    wrong_pkt = RawPacket(
        trace_id="flow-probe-bad-edge",
        ts_ms=2000,
        source="connect.camera",
        payload=b"wrong",
        sensor_kind="camera",
    )
    edge_err = engine.send_across_edge("signal", "process.sampler", wrong_pkt, trace_id="flow-probe-bad-edge")
    if (
        not isinstance(edge_err, ErrorEvent)
        or edge_err.code != "E_FLOW_EDGE_TYPE"
        or edge_err.node_path != "signal->process.sampler"
    ):
        print(f"[FAIL] Bơm gói sai kiểu không bắt đúng cạnh: {edge_err}")
        return 1

    assert engine.camera is not None
    engine.camera.simulate_disconnect()
    lost_err = engine.step_once(trace_id="flow-probe-cam-lost")
    if (
        not isinstance(lost_err, ErrorEvent)
        or lost_err.code != "E_CONN_LOST"
        or lost_err.node_path != "connect.camera"
    ):
        print(f"[FAIL] Ngắt cam giả không báo E_CONN_LOST đúng nodePath: {lost_err}")
        return 1

    latest_diag = engine.diagnose.latest()
    if latest_diag is None or latest_diag.code != "E_CONN_LOST":
        print("[FAIL] fd_diagnose không ghi nhận được ErrorEvent từ fd_flow")
        return 1

    print(
        f"[PASS] flow_trace: step_once -> DetectionResult(frame_id={step_res.frame_id}) | "
        f"sai kiểu cạnh -> {edge_err.code} tại {edge_err.node_path} | "
        f"ngắt cam -> {lost_err.code} tại {lost_err.node_path} | "
        f"diagnose_count={engine.diagnose.count}"
    )
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(run_flow_probe())
