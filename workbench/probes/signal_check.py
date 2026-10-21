#!/usr/bin/env python3
"""Trạm thử nghiệm độc lập cho fd_signal (workbench/probes/signal_check.py).

- Bơm gói hợp lệ, ảnh đen (luma=0), ảnh sai kích thước và lệch timestamp để xác minh mã lỗi.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from contracts.logic import ErrorEvent, RawPacket, SignalPacket
from fd_connect.camera_source import encode_raw_frame
from fd_signal.frame_normalizer import FrameNormalizer


def run_signal_probe() -> int:
    norm = FrameNormalizer(expected_width=640, expected_height=480, max_drift_ms=2000)

    valid_raw = RawPacket(
        trace_id="sig-probe-ok",
        ts_ms=1000,
        source="connect.camera",
        payload=encode_raw_frame(640, 480, 1, luma_mean=128),
        sensor_kind="camera",
    )
    sig_ok = norm.normalize(valid_raw, driver_id="drv-01")
    if not isinstance(sig_ok, SignalPacket) or sig_ok.frame_id != 1:
        print("[FAIL] Gói chuẩn không tạo được SignalPacket")
        return 1

    black_raw = RawPacket(
        trace_id="sig-probe-black",
        ts_ms=1100,
        source="connect.camera",
        payload=encode_raw_frame(640, 480, 2, luma_mean=0),
        sensor_kind="camera",
    )
    err_black = norm.normalize(black_raw)
    if not isinstance(err_black, ErrorEvent) or err_black.code != "E_SIG_EMPTY":
        print("[FAIL] Ảnh đen không phát mã E_SIG_EMPTY")
        return 1

    bad_size_raw = RawPacket(
        trace_id="sig-probe-size",
        ts_ms=1200,
        source="connect.camera",
        payload=encode_raw_frame(320, 240, 3, luma_mean=120),
        sensor_kind="camera",
    )
    err_size = norm.normalize(bad_size_raw)
    if not isinstance(err_size, ErrorEvent) or err_size.code != "E_SIG_BAD_FORMAT":
        print("[FAIL] Ảnh sai kích thước không phát mã E_SIG_BAD_FORMAT")
        return 1

    drift_raw = RawPacket(
        trace_id="sig-probe-drift",
        ts_ms=900,
        source="connect.camera",
        payload=encode_raw_frame(640, 480, 4, luma_mean=120),
        sensor_kind="camera",
    )
    err_drift = norm.normalize(drift_raw)
    if not isinstance(err_drift, ErrorEvent) or err_drift.code != "E_SIG_TS_DRIFT":
        print("[FAIL] Timestamp lùi không phát mã E_SIG_TS_DRIFT")
        return 1

    print(
        f"[PASS] signal_check: SignalPacket(frame_id={sig_ok.frame_id}) | "
        f"ảnh đen -> {err_black.code} | sai cỡ -> {err_size.code} | "
        f"ts lệch -> {err_drift.code} (node_path={err_drift.node_path})"
    )
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(run_signal_probe())
