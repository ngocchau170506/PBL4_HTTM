#!/usr/bin/env python3
"""Script đo đạc và nghiệm thu cơ chế Phân mảnh ảnh (Chunking) & Flow Control (FEAT-113 / G11)."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "workbench"))

from fd_connect.fd_frame import (
    FRAME_TYPE_IMAGE_CHUNK,
    decode_fd_frame,
    encode_fd_frame,
)
from fd_response.esp32_network_bridge import ChunkAssembler, Esp32NetworkBridge
from fd_response.server_dispatcher import (
    DispatchPackage,
    FrameChunk,
    create_chunks_from_package,
)
from mock_esp32_gateway import MockEsp32TelematicsGateway


def run_benchmark() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    print("=" * 65)
    print("  ĐỐI CHUẨN & NGHIỆM THU G11: CHUNKING & FLOW CONTROL (FEAT-113)")
    print("=" * 65)

    tracemalloc.start()
    t_start = time.perf_counter()

    # 1. Tạo gói DispatchPackage mô phỏng 10 ảnh JPEG vi phạm
    evidence_frames = [f"JPEG_DATA_SIMULATION_FRAME_{i:03d}_{'X'*1024}".encode("utf-8") for i in range(1, 11)]
    pkg = DispatchPackage(
        trace_id="trace-bench-chunk-01",
        rule="eye_drowsiness",
        t0_ms=1000,
        pre_ms=0,
        ts_ms=1500,
        frame_ids=tuple(range(1, 11)),
        evidence_frames=tuple(evidence_frames),
        telemetry={"gps_lat": 10.776889, "gps_lon": 106.700806, "speed_kmh": 46.5},
    )

    chunks = create_chunks_from_package(pkg, device_id="CAR-09-HCM")
    print(f"[1] Số lượng mảnh ảnh tạo ra: {len(chunks)} chunks (tương ứng 10 frames)")

    # 2. Khởi tạo Gateway & Cầu nối Pi
    gateway = MockEsp32TelematicsGateway(wifi_connected=True, partner_online=False, host_pc_online=False)
    bridge = Esp32NetworkBridge(
        uart_sender=lambda b: bool(gateway.ingest_bytes(b) is not None),
        frame_encoder=encode_fd_frame,
    )

    # 3. Truyền tải 100 chu kỳ phân mảnh (1,000 chunks) để đo thông lượng
    total_cycles = 100
    for cycle in range(total_cycles):
        for c in chunks:
            bridge.send_image_chunk(c)

    elapsed_s = time.perf_counter() - t_start
    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    total_chunks_processed = total_cycles * len(chunks)
    throughput_chunks_per_sec = total_chunks_processed / elapsed_s
    delta_ram_mb = peak_mem / (1024 * 1024)

    print(f"[2] Tổng số chunks truyền tải: {total_chunks_processed:,} chunks")
    print(f"[3] Thời gian xử lý: {elapsed_s:.4f} giây")
    print(f"[4] Thông lượng xử lý: {throughput_chunks_per_sec:,.1f} chunks/giây")
    print(f"[5] Đỉnh bộ nhớ tiêu thụ: {delta_ram_mb:.4f} MB (ngưỡng tối đa 5.0 MB)")
    print(f"[6] Còi báo động trên Gateway: {'ĐÃ BẬT' if gateway.buzzer_active else 'CHƯA'}")

    # Nghiệm thu lắp ráp thành công
    assembler = ChunkAssembler(expected_total=len(chunks), incident_id=chunks[0].incident_id)
    for c in chunks:
        assembler.add_chunk(c)
    assembled = assembler.assemble()
    is_ok = assembled is not None and len(assembled) == len(chunks)
    print(f"[7] Kiểm tra lắp ráp toàn vẹn: {'ĐẠT (PASS)' if is_ok else 'THẤT BẠI (FAIL)'}")

    print("=" * 65)
    print(f"KẾT LUẬN G11 CHUNKING BENCHMARK: {'HOÀN TẤT ĐẠT CHUẨN' if is_ok and delta_ram_mb < 5.0 else 'CÓ LỖI'}")
    return 0 if (is_ok and delta_ram_mb < 5.0) else 1


if __name__ == "__main__":
    sys.exit(run_benchmark())
