#!/usr/bin/env python3
"""Trạm thử nghiệm độc lập cho fd_connect (workbench/probes/connect_probe.py).

- Nạp cấu hình từ fd_config, mở CameraSource, đọc gói thử nghiệm, đếm gói và thử ngắt kết nối giả.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from contracts.logic import ErrorEvent, RawPacket
from fd_config.config_loader import ConfigLoader
from fd_connect.camera_source import CameraSource


def run_probe(sample_packets: int = 5) -> int:
    loader = ConfigLoader()
    cfg = loader.load(trace_id="probe-cfg")
    if isinstance(cfg, ErrorEvent):
        print(f"[FAIL] Nạp cấu hình thất bại: {cfg.code} - {cfg.message}")
        return 1

    cam = CameraSource(
        source=cfg.camera.source,
        index=cfg.camera.index,
        uri=cfg.camera.uri,
        fps=cfg.camera.fps,
        width=cfg.camera.width,
        height=cfg.camera.height,
    )

    open_err = cam.open(trace_id="probe-cam-open")
    if open_err is not None:
        print(f"[FAIL] Không mở được nguồn: {open_err.code} ({open_err.node_path})")
        return 1

    received = 0
    for idx in range(1, sample_packets + 1):
        pkt = cam.read_packet(trace_id=f"probe-pkt-{idx:03d}")
        if not isinstance(pkt, RawPacket):
            print(f"[FAIL] Gói #{idx} trả lỗi ngoài ý muốn: {pkt.code}")
            return 1
        received += 1

    cam.simulate_disconnect()
    lost_evt = cam.read_packet(trace_id="probe-pkt-disconnect")
    if not isinstance(lost_evt, ErrorEvent) or lost_evt.code != "E_CONN_LOST":
        print("[FAIL] Ngắt kết nối giả không phát ra E_CONN_LOST")
        return 1

    print(
        f"[PASS] connect_probe: đã nhận {received} RawPacket | "
        f"ngắt giả -> {lost_evt.code} tại node_path={lost_evt.node_path} | "
        f"health={cam.health()}"
    )
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(run_probe())
