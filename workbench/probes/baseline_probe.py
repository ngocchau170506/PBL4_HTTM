"""Probe kiểm thử độc lập bộ thu thập và lưu trữ Baseline (FEAT-021, FEAT-022).

Chạy: python workbench/probes/baseline_probe.py
Kiểm tra:
  1. Tài xế A thu thập 5s ở nhịp 5 FPS (26 frame) -> khóa cứng LOCKED, các frame sau không làm đổi hồ sơ.
  2. Đổi sang Tài xế B giữa chừng -> Tài xế B học hồ sơ mới riêng biệt, hồ sơ A giữ nguyên.
  3. Xuất/nhập JSON hồ sơ baseline giữa 2 kho lưu trữ -> bảo toàn 100% dữ liệu.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_process.baseline.baseline_collector import BaselineCollector
from fd_process.baseline.baseline_storage import BaselineStorage


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    collector = BaselineCollector(seconds_min=5, seconds_max=10, min_frames=10)

    for i in range(40):
        pkt = L.SignalPacket(
            trace_id=f"probe-drvA-{i + 1}",
            ts_ms=1000 + i * 200,
            source="process.sampler",
            frame_id=i + 1,
            width=640,
            height=480,
            driver_id="driver-A",
        )
        res = collector.process_frame(pkt)
        if isinstance(res, L.ErrorEvent):
            print(f"FAIL driver-A: {res}")
            return 1

    prof_a = collector.current_profile("driver-A")
    if prof_a is None or prof_a.state != "LOCKED" or prof_a.duration_ms != 5000 or prof_a.frame_count != 26:
        print(f"FAIL: Hồ sơ driver-A không khóa đúng mốc 5000ms (26 frames): {prof_a}")
        return 1
    print(f"[BASELINE PROBE] Tài xế A: state={prof_a.state}, duration={prof_a.duration_ms}ms, frames={prof_a.frame_count} (đã khóa cố định dù chạy thêm 14 frame)")

    for j in range(30):
        pkt_b = L.SignalPacket(
            trace_id=f"probe-drvB-{j + 1}",
            ts_ms=20000 + j * 250,
            source="process.sampler",
            frame_id=100 + j,
            width=640,
            height=480,
            driver_id="driver-B",
        )
        res_b = collector.process_frame(pkt_b)
        if isinstance(res_b, L.ErrorEvent):
            print(f"FAIL driver-B: {res_b}")
            return 1

    prof_b = collector.current_profile("driver-B")
    prof_a_after = collector.current_profile("driver-A")
    if prof_b is None or prof_b.state != "LOCKED" or prof_a_after != prof_a:
        print(f"FAIL: Lẫn dữ liệu khi đổi tài xế: A={prof_a_after}, B={prof_b}")
        return 1
    print(f"[BASELINE PROBE] Đổi sang Tài xế B: state={prof_b.state}, duration={prof_b.duration_ms}ms, frames={prof_b.frame_count} | Hồ sơ A giữ nguyên 100%")

    raw_json = collector.storage.export_json()
    restored_store = BaselineStorage()
    imp_err = restored_store.import_json(raw_json, trace_id="probe-import")
    if imp_err is not None or restored_store.get("driver-A") != prof_a or restored_store.get("driver-B") != prof_b:
        print(f"FAIL: Xuất/nhập JSON thất bại: {imp_err}")
        return 1
    print(f"[BASELINE PROBE] Lưu/đọc JSON cho {restored_store.count} tài xế ({restored_store.driver_ids()}) -> PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
