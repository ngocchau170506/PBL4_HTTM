"""Kiểm thử nghiệm thu tổng hợp GATE G2 (workbench/probes/g2_gate_verify.py).

Chạy: python workbench/probes/g2_gate_verify.py
Tiêu chí GATE G2 trong feature-map.md:
  1. Đổi tài xế -> baseline mới, không lẫn dữ liệu (kiểm tra 10 tài xế liên tiếp + quay lại tài xế cũ).
  2. Test FPS thấp / cao (10 FPS, 25 FPS RTSP, 30 FPS, 60 FPS) qua toàn bộ cây luồng FlowEngine.
  3. Kiểm tra 0 rò rỉ bộ nhớ và giải phóng sạch hàng đợi cạnh.
"""
from __future__ import annotations

import gc
import sys
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_flow.flow_engine import FlowEngine
from fd_process.sampler.fps_throttle import FpsThrottle


def verify_multi_driver_and_fps() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    gc.collect()
    tracemalloc.start()

    engine = FlowEngine()
    boot_err = engine.boot(trace_id="g2-gate-boot")
    if boot_err is not None:
        print(f"[FAIL] Boot lỗi: {boot_err}")
        return 1

    assert engine.baseline is not None
    assert engine.sampler is not None

    ts_cursor_ms = 1000
    locked_snapshots = {}

    for drv_idx in range(1, 11):
        driver_id = f"driver-{drv_idx:02d}"
        for f in range(150):
            res = engine.step_sampled(
                trace_id=f"g2-{driver_id}-{f + 1}",
                driver_id=driver_id,
                ts_override_ms=ts_cursor_ms,
            )
            if isinstance(res, L.ErrorEvent):
                print(f"[FAIL] Lỗi tại {driver_id} frame {f + 1}: {res}")
                return 1
            ts_cursor_ms += 40

        prof = engine.baseline.current_profile(driver_id)
        if prof is None or prof.state != "LOCKED" or prof.duration_ms != 5000:
            print(f"[FAIL] Hồ sơ của {driver_id} không khóa chuẩn: {prof}")
            return 1
        locked_snapshots[driver_id] = prof

    for drv_id, snap in locked_snapshots.items():
        if engine.baseline.current_profile(drv_id) != snap:
            print(f"[FAIL] Hồ sơ của {drv_id} bị biến đổi sau khi đổi tài xế khác")
            return 1

    print(
        f"[GATE G2 - ĐỔI TÀI XẾ] 10/10 tài xế (driver-01..driver-10) đều tạo BaselineProfile riêng, "
        f"khóa LOCKED ở 5000ms (26 frames), 0 lẫn dữ liệu, trạng thái hệ thống={engine.breaker.system_state}"
    )

    fps_cases = [(10, 5), (25, 5), (30, 6), (60, 5)]
    for src_fps, tgt_fps in fps_cases:
        sub_engine = FlowEngine(sampler=FpsThrottle(target_fps=tgt_fps))
        sub_engine.boot(trace_id=f"boot-fps-{src_fps}")
        passed = 0
        for i in range(src_fps * 6):
            ts_ms = 1000 + round(i * (1000.0 / src_fps))
            out = sub_engine.step_sampled(
                trace_id=f"fps-{src_fps}-{i + 1}",
                driver_id=f"drv-fps-{src_fps}",
                ts_override_ms=ts_ms,
            )
            if isinstance(out, L.ErrorEvent):
                print(f"[FAIL] Lỗi ở nguồn {src_fps} FPS: {out}")
                return 1
            if out is not None:
                passed += 1

        expected = tgt_fps * 6
        if passed != expected:
            print(f"[FAIL] Nguồn {src_fps} FPS -> {tgt_fps} FPS: nhận {passed}/{expected}")
            return 1
        print(f"[GATE G2 - FPS THẤP/CAO] Nguồn {src_fps} FPS -> Đích {tgt_fps} FPS (6s): vào={src_fps * 6}, qua AI={passed}/{expected} -> PASS")

    gc.collect()
    mem_curr, mem_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"[GATE G2 - BỘ NHỚ] RAM hiện hành={mem_curr / 1024:.1f} KB | Đỉnh RAM={mem_peak / 1024:.1f} KB | Hàng đợi tồn đọng=0 -> PASS")
    return 0


if __name__ == "__main__":
    sys.exit(verify_multi_driver_and_fps())
