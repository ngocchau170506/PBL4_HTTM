#!/usr/bin/env python3
"""Script Khởi Chạy 1-Chạm Máy Chủ Tiếp Nhận & Quản Lý Dữ Liệu Xe (Fleet Management Server).

Vận hành trên Máy tính PC hoặc Máy chủ Đám mây:
- Lắng nghe mọi kết nối từ Raspberry Pi 4 (IOTv2/IOTv3/ESP32).
- Mở Web Dashboard giám sát hạm đội xe thời gian thực.
- Lưu trữ an toàn cơ sở dữ liệu SQLite và thư mục ảnh vi phạm.

Cách sử dụng:
    python run_server.py                 # Chạy mặc định cổng 5000
    python run_server.py --port 5000     # Chỉ định cổng tùy chọn
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from app.server import FleetServer, get_local_ip


def main():
    parser = argparse.ArgumentParser(description="Fleet Management Server (Máy Chủ Tiếp Nhận Dữ Liệu Xe)")
    parser.add_argument("--host", default="0.0.0.0", help="Địa chỉ lắng nghe (mặc định 0.0.0.0 - toàn bộ card mạng)")
    parser.add_argument("--port", type=int, default=5000, help="Cổng dịch vụ HTTP API & Dashboard (mặc định 5000)")
    args = parser.parse_args()

    pc_ip = get_local_ip()

    print("=" * 80)
    print("🚀 MÁY CHỦ TIẾP NHẬN & QUẢN LÝ XE TẬP TRUNG (FLEET MANAGEMENT SERVER)")
    print("=" * 80)
    print(f"👉 Web Dashboard Giám Sát Cục Bộ:       http://localhost:{args.port}")
    print(f"👉 Địa Chỉ Tiếp Nhận Dành Cho Pi (LAN):  http://{pc_ip}:{args.port}")
    print("--------------------------------------------------------------------------------")
    print("📋 HƯỚNG DẪN CẤU HÌNH TRÊN XE RASPBERRY PI 4 (IOTv2 / IOTv3):")
    print(f"   Mở file AI/deploy/pi.env trên Pi và thiết lập:")
    print(f"      REMOTE_SERVER_ENABLED=1")
    print(f"      REMOTE_SERVER_URL=http://{pc_ip}:{args.port}")
    print("--------------------------------------------------------------------------------")
    print("⚡ CÁC CỔNG TIẾP NHẬN SẴN SÀNG:")
    print(f"   + Nhịp tim Telemetry (ESP + GPS + MQ135 + Pi):  POST /api/v1/telemetry")
    print(f"   + Bằng chứng Vi phạm kèm ảnh JPEG Base64:       POST /api/v1/violation")
    print(f"   + Lệnh điều khiển hai chiều xuống xe:          GET  /api/v1/pi/pull_commands")
    print("=" * 80)
    print("🟢 Đang lắng nghe luồng dữ liệu... Nhấn Ctrl+C để dừng.\n")

    server = FleetServer(host=args.host, port=args.port)
    try:
        server.run_forever()
    except KeyboardInterrupt:
        print("\n🛑 Đang dừng Máy Chủ...")
        server.stop()
        print("✅ Đã giải phóng toàn bộ tài nguyên Server.")


if __name__ == "__main__":
    main()
