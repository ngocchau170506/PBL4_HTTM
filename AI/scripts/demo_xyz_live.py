"""Kịch bản trình diễn trực tiếp: Máy chủ định vị & phát tọa độ không gian 3 chiều XYZ xuống Xe (DEMO-LIVE-XYZ).

Chạy trực tiếp:
    python AI/scripts/demo_xyz_live.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request

# Đảm bảo bảng mã UTF-8 cho console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

repo_root = Path(__file__).resolve().parents[2]
for p in (str(repo_root), str(repo_root / "AI"), str(repo_root / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from AI.scripts.cloud_sync import CloudSyncManager
from AI.scripts.gps_service import GpsManager
from AI.scripts.mock_friend_server import FriendServerHandler, ThreadingHTTPServer, CONNECTED_VEHICLES


def print_banner(text: str):
    print("\n" + "=" * 72)
    print(f"  {text}")
    print("=" * 72)


def main():
    print_banner("DEMO TRỰC TIẾP: HỆ THỐNG ĐỊNH VỊ 3D XYZ QUA MÁY CHỦ (SERVER-ASSISTED)")

    test_port = 5055
    server = ThreadingHTTPServer(("127.0.0.1", test_port), FriendServerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"[1. KHỞI TẠO] Máy Chủ Bạn Tôi (Central Server) đã chạy tại: http://127.0.0.1:{test_port}")

    # Khởi tạo phía Xe (Pi / Host)
    gps_mgr = GpsManager(simulation_mode=False)
    client = CloudSyncManager(
        server_url=f"http://127.0.0.1:{test_port}",
        enabled=True,
        telemetry_interval=1.0,
        gps_manager=gps_mgr,
    )
    print(f"[2. KHỞI TẠO] Máy Chủ Xe (Pi Client) đã khởi động với ID: {client.access_info.vehicle_id}")

    # Bước 1: Trạng thái ban đầu khi chưa có sóng vệ tinh trong phòng lab
    print("\n" + "-" * 72)
    print("GIAI ĐOẠN 1: XE TRONG PHÒNG LAB / HẦM KHÔNG CÓ SÓNG VỆ TINH NGOÀI TRỜI")
    print("-" * 72)
    initial_snap = gps_mgr.get_snapshot()
    print(f"  - Trạng thái GPS ban đầu : {initial_snap['status']} (Số vệ tinh: {initial_snap['satellites']})")
    print(f"  - Nguồn định vị          : {initial_snap['source']}")
    print(f"  - Hợp lệ (Valid fix)     : {initial_snap['is_valid']}")
    print("  -> Xe chưa thể định vị nếu chỉ dựa vào vệ tinh GNSS!")

    time.sleep(1.0)

    # Danh sách các trạm tọa độ XYZ máy chủ sẽ lần lượt phát xuống xe
    test_locations = [
        {
            "name": "Trạm 1: TP. Hồ Chí Minh (Đồng bằng - Cao độ 12m)",
            "lat": 10.772500,
            "lon": 106.701200,
            "alt_m": 12.0,
            "speed_kmh": 35.0,
        },
        {
            "name": "Trạm 2: Thủ Đô Hà Nội (Đồng bằng Bắc Bộ - Cao độ 18m)",
            "lat": 21.028500,
            "lon": 105.854200,
            "alt_m": 18.0,
            "speed_kmh": 42.0,
        },
        {
            "name": "Trạm 3: TP. Đà Nẵng (Ven biển miền Trung - Cao độ 8m)",
            "lat": 16.061200,
            "lon": 108.227200,
            "alt_m": 8.0,
            "speed_kmh": 48.0,
        },
        {
            "name": "Trạm 4: Đỉnh Đèo Đà Lạt (Vùng núi cao nguyên - Cao độ 1,500m)",
            "lat": 11.940400,
            "lon": 108.458300,
            "alt_m": 1500.0,
            "speed_kmh": 28.5,
        },
    ]

    for idx, loc in enumerate(test_locations, start=2):
        print("\n" + "-" * 72)
        print(f"GIAI ĐOẠN {idx}: MÁY CHỦ PHÁT TỌA ĐỘ X, Y, Z XUỐNG XE")
        print(f"  Địa điểm: {loc['name']}")
        print("-" * 72)

        # 1. Máy chủ cập nhật tọa độ Downlink phát đi
        req_body = json.dumps({
            "lat": loc["lat"],
            "lon": loc["lon"],
            "alt_m": loc["alt_m"],
            "speed_kmh": loc["speed_kmh"],
        }).encode("utf-8")
        inject_req = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/v1/geo/inject_xyz",
            data=req_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(inject_req, timeout=2.0) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            print(f"  [SERVER] Đã nạp phát trạm định vị: {resp_data.get('ack')}")

        # 2. Xe gửi 1 nhịp Telemetry/Heartbeat lên Server
        #    Server sẽ tự động nhúng Downlink XYZ vào ACK trả về
        payload = client.build_telemetry_payload()
        ok, ack = client._http_post("/api/v1/telemetry", payload)
        time.sleep(0.2)

        # 3. Đọc dữ liệu định vị hiện thời trên xe
        snap = gps_mgr.get_snapshot()

        print(f"  [CLIENT XE ĐÃ NHẬN TỌA ĐỘ 3 CHIỀU TỪ SERVER]:")
        print(f"  ┌─────────────────────────────────────────────────────────────┐")
        print(f"  │  Trục X (Kinh độ Lon) : {snap['x']:>12.6f}° Đông               │")
        print(f"  │  Trục Y (Vĩ độ Lat)  : {snap['y']:>12.6f}° Bắc                │")
        print(f"  │  Trục Z (Cao độ Alt) : {snap['z']:>12.1f} mét so với mặt biển │")
        print(f"  │  Vận tốc di chuyển   : {snap['speed_kmh']:>12.1f} km/h                 │")
        print(f"  │  Nguồn định vị       : {snap['source'].upper():>12} (Server-Assisted)    │")
        print(f"  │  Trạng thái Fix      : {snap['status']:>12}                       │")
        print(f"  │  Google Maps Link    : {snap['google_maps_url']}│")
        print(f"  └─────────────────────────────────────────────────────────────┘")

        time.sleep(0.8)

    # Bước cuối: Kiểm tra gắn tọa độ XYZ vào sự kiện vi phạm
    print("\n" + "-" * 72)
    print("GIAI ĐOẠN 6: GẮN TỌA ĐỘ XYZ VÀO SỰ KIỆN CẢNH BÁO VI PHẠM THỜI GIAN THỰC")
    print("-" * 72)
    import numpy as np
    import cv2

    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(dummy_frame, "CANH BAO NGU GAT - XYZ VERIFIED", (30, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)

    client.dispatch_violation(
        alert_type="DROWSY_DANGER",
        frame=dummy_frame,
        duration_seconds=3.2,
        confidence=0.98,
    )
    time.sleep(0.6)

    # Kiểm tra xem Server đã lưu sự kiện kèm tọa độ XYZ của Đèo Đà Lạt chưa
    from AI.scripts.mock_friend_server import VIOLATION_HISTORY
    if VIOLATION_HISTORY:
        last_evt = VIOLATION_HISTORY[-1]
        print(f"  [SERVER BAN TOI NHẬN VI PHẠM]:")
        print(f"  - Mã sự kiện: {last_evt.get('event_id')}")
        print(f"  - Loại vi phạm: {last_evt.get('alert_type')} (Thời lượng: {last_evt.get('duration_seconds')}s)")
        print(f"  - Tọa độ GPS gắn kèm: X={last_evt['gps']['x']}, Y={last_evt['gps']['y']}, Z={last_evt['gps']['z']}m")
        print(f"  - Nguồn tọa độ: {last_evt['gps']['source']}")
        print(f"  - Có ảnh bằng chứng Base64: {'ĐẠT (Base64 JPEG)' if last_evt.get('evidence_image') else 'KHÔNG'}")

    client.close()
    server.shutdown()
    server.server_close()

    print_banner("HOÀN TẤT DEMO: TỌA ĐỘ 3D XYZ HOẠT ĐỘNG HOÀN HẢO MÀ KHÔNG CẦN VỆ TINH!")


if __name__ == "__main__":
    main()
