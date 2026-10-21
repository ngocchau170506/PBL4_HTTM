"""Probe kiểm thử độc lập bộ lấy mẫu nhịp FpsThrottle (FEAT-020).

Chạy: python workbench/probes/sampler_probe.py
Kiểm tra:
  1. Nguồn 25 FPS (Camera RTSP thật) -> lọc đúng 5 FPS.
  2. Nguồn 30 FPS -> lọc đúng 6 FPS.
  3. Nguồn 60 FPS -> lọc đúng 5 FPS.
  4. Bơm lỗi timestamp lùi -> bắt đúng E_SIG_TS_DRIFT tại node_path='process.sampler'.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_process.sampler.fps_throttle import FpsThrottle


def run_rate_trial(source_fps: int, target_fps: int, duration_s: int = 10) -> dict:
    throttle = FpsThrottle(target_fps=target_fps)
    total_frames = source_fps * duration_s
    passed = 0
    for i in range(total_frames):
        ts_ms = 1000 + round(i * (1000.0 / source_fps))
        pkt = L.SignalPacket(
            trace_id=f"probe-s{source_fps}-t{target_fps}-{i + 1}",
            ts_ms=ts_ms,
            source="signal",
            frame_id=i + 1,
            width=640,
            height=480,
            driver_id="drv-probe",
        )
        res = throttle.filter_packet(pkt)
        if isinstance(res, L.ErrorEvent):
            raise RuntimeError(f"Lỗi không mong muốn: {res}")
        if res is not None:
            passed += 1

    st = throttle.stats()
    return {
        "source_fps": source_fps,
        "target_fps": target_fps,
        "duration_s": duration_s,
        "total_in": st.total_in,
        "total_passed": st.total_passed,
        "total_dropped": st.total_dropped,
        "expected_passed": target_fps * duration_s,
        "measured_output_fps": st.measured_output_fps,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    trials = [
        run_rate_trial(source_fps=25, target_fps=5, duration_s=10),
        run_rate_trial(source_fps=30, target_fps=6, duration_s=10),
        run_rate_trial(source_fps=60, target_fps=5, duration_s=10),
        run_rate_trial(source_fps=10, target_fps=5, duration_s=10),
    ]

    for t in trials:
        print(
            f"[SAMPLER PROBE] {t['source_fps']} FPS -> {t['target_fps']} FPS ({t['duration_s']}s): "
            f"vào={t['total_in']}, qua={t['total_passed']}/{t['expected_passed']}, "
            f"lược bỏ={t['total_dropped']}, đo thực={t['measured_output_fps']} FPS"
        )
        if t["total_passed"] != t["expected_passed"]:
            print("FAIL: Số lượng frame qua cổng lệch kỳ vọng")
            return 1

    throttle = FpsThrottle(target_fps=5)
    ok_pkt = L.SignalPacket("t-1", 2000, "signal", 1, 640, 480)
    drift_pkt = L.SignalPacket("t-2", 1800, "signal", 2, 640, 480)
    throttle.filter_packet(ok_pkt)
    err = throttle.filter_packet(drift_pkt)
    if not isinstance(err, L.ErrorEvent) or err.code != "E_SIG_TS_DRIFT" or err.node_path != "process.sampler":
        print(f"FAIL: Không bắt đúng E_SIG_TS_DRIFT: {err}")
        return 1

    print(f"[SAMPLER PROBE] Bắt lỗi trôi ngược thời gian: {err.code} tại node_path={err.node_path} -> PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
