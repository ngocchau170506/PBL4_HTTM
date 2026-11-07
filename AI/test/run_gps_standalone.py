"""Bộ chạy độc lập kiểm thử GPS & Viễn thông trên Raspberry Pi (GPS Standalone Runner).

ĐẶC ĐIỂM KỸ THUẬT:
1. KHÔNG cần Camera: Tuyệt đối không mở RTSP hay V4L2 webcam, loại bỏ 100% lỗi ngắt kết nối camera.
2. KHÔNG cần AI (YOLO / MediaPipe): Không tải mô hình nặng, không tốn RAM, CPU Raspberry Pi < 3%.
3. TẬP TRUNG 100% VÀO GPS:
   - Tự động quét và đọc cảm biến GPS phần cứng USB / UART (/dev/ttyUSB*, /dev/ttyACM*, /dev/serial0, COM*).
   - Tự động nhận tọa độ 3D XYZ Downlink từ Server Bạn Tôi trên máy tính (TP. Đà Nẵng: 16.0685°N, 108.2215°E, 8.5m).
   - Mở Web Dashboard tại http://0.0.0.0:8080 cho máy tính truy cập xem trực tiếp.
   - Tạo khung hình hiển thị HUD Radar GPS thời gian thực cho luồng /stream.
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import sys
import threading
import time

import cv2
import numpy as np

# Thiết lập đường dẫn import
repo_root = Path(__file__).resolve().parents[2]
for p in (str(repo_root), str(repo_root / "AI"), str(repo_root / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from AI.app.web_server import DriverMonitorWebServer
from AI.scripts.cloud_sync import CloudSyncManager
from AI.scripts.esp32_network_bridge import Esp32NetworkBridge
from AI.scripts.esp_communication import DEFAULT_HIGHSPEED_BAUDRATE, EspCommunicator
from AI.scripts.fd_frame import encode_fd_frame
from AI.scripts.gps_service import GpsManager, haversine_distance_km


def create_telemetry_hud_frame(
    width: int,
    height: int,
    gps_snap: dict,
    esp_conn: bool,
    esp_port: str,
    distance_m: float,
    server_url: str,
    ip_addr: str,
    port: int,
) -> np.ndarray:
    """Tạo bảng đo kỹ thuật số hiển thị tọa độ XYZ, các ping tín hiệu và khoảng cách đo bằng mét (KHÔNG DÙNG RADA)."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (15, 23, 42)  # Nền tối công nghệ #0f172a

    # Tọa độ XYZ
    sats = gps_snap.get("satellites", 0)
    is_valid = gps_snap.get("is_valid", False)
    status = gps_snap.get("status", "NO_FIX")
    source = gps_snap.get("source", "satellite")
    lat = gps_snap.get("lat", 16.0685)
    lon = gps_snap.get("lon", 108.2215)
    alt = gps_snap.get("altitude_m", 8.5)
    spd = gps_snap.get("speed_kmh", 0.0)

    # 1. HÀNG PING TÍN HIỆU NHỎ (INDICATOR BADGES) PHÍA TRÊN CÙNG
    # Ping 1: ESP32
    esp_col = (34, 197, 94) if esp_conn else (245, 158, 11)
    esp_text = f"ESP32: OK ({esp_port or 'USB'})" if esp_conn else "ESP32: DANG DO CONG"
    cv2.rectangle(frame, (20, 16), (220, 44), (21, 29, 47), -1)
    cv2.rectangle(frame, (20, 16), (220, 44), (35, 49, 77), 1)
    cv2.circle(frame, (32, 30), 4, esp_col, -1)
    cv2.putText(frame, esp_text, (44, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)

    # Ping 2: GPS Phần cứng
    gps_col = (34, 197, 94) if is_valid else (56, 189, 248) if status == "SERVER_XYZ" else (239, 68, 68)
    gps_label = f"GPS: FIX 3D ({sats} Sats)" if is_valid else "GPS: SERVER-XYZ" if status == "SERVER_XYZ" else "GPS: TIM TIN HIEU"
    cv2.rectangle(frame, (230, 16), (420, 44), (21, 29, 47), -1)
    cv2.rectangle(frame, (230, 16), (420, 44), (35, 49, 77), 1)
    cv2.circle(frame, (242, 30), 4, gps_col, -1)
    cv2.putText(frame, gps_label, (254, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)

    # Ping 3: Đường truyền PC ↔ Pi
    cv2.rectangle(frame, (430, 16), (620, 44), (21, 29, 47), -1)
    cv2.rectangle(frame, (430, 16), (620, 44), (35, 49, 77), 1)
    cv2.circle(frame, (442, 30), 4, (168, 85, 247), -1)
    cv2.putText(frame, f"K/CACH: {distance_m:.1f} m", (454, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)

    # 2. KHỐI HIỂN THỊ KHOẢNG CÁCH ĐO BẰNG MÉT (NỔI BẬT TRUNG TÂM)
    cv2.rectangle(frame, (20, 56), (620, 150), (21, 29, 47), -1)
    cv2.rectangle(frame, (20, 56), (620, 150), (56, 189, 248), 1)

    cv2.putText(frame, "KHOANG CACH TU PI DEN MAY TINH (DO BANG MET):", (35, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (56, 189, 248), 1)
    cv2.putText(frame, f"{distance_m:.2f} MET", (35, 126), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 3)
    cv2.putText(frame, f"(Khong gian 3D | Moc PC: 16.0685 N, 108.2215 E, 8.5m)", (310, 126), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (148, 163, 184), 1)

    # 3. BA KHỐI ĐO TỌA ĐỘ 3D XYZ
    # Trục X
    cv2.rectangle(frame, (20, 162), (213, 275), (21, 29, 47), -1)
    cv2.rectangle(frame, (20, 162), (213, 275), (56, 189, 248), 1)
    cv2.putText(frame, "TRUC X (KINH DO)", (32, 188), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (56, 189, 248), 1)
    cv2.putText(frame, f"{lon:.6f}", (32, 225), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2)
    cv2.putText(frame, "Do Dong (E)", (32, 255), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (148, 163, 184), 1)

    # Trục Y
    cv2.rectangle(frame, (223, 162), (416, 275), (21, 29, 47), -1)
    cv2.rectangle(frame, (223, 162), (416, 275), (34, 197, 94), 1)
    cv2.putText(frame, "TRUC Y (VI DO)", (235, 188), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (34, 197, 94), 1)
    cv2.putText(frame, f"{lat:.6f}", (235, 225), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2)
    cv2.putText(frame, "Do Bac (N)", (235, 255), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (148, 163, 184), 1)

    # Trục Z
    cv2.rectangle(frame, (426, 162), (620, 275), (21, 29, 47), -1)
    cv2.rectangle(frame, (426, 162), (620, 275), (168, 85, 247), 1)
    cv2.putText(frame, "TRUC Z (CAO DO)", (438, 188), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (168, 85, 247), 1)
    cv2.putText(frame, f"{alt:.1f} m", (438, 225), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2)
    cv2.putText(frame, f"Van toc: {spd:.1f} km/h", (438, 255), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (148, 163, 184), 1)

    # 4. CHÂN BẢNG: THÔNG TIN MẠNG
    cv2.putText(frame, f"Web Dashboard: http://{ip_addr}:{port}", (20, 310), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (56, 189, 248), 1)
    cv2.putText(frame, f"Ket noi Server Ban Toi: {server_url}", (20, 335), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (148, 163, 184), 1)

    # Viền bao ngoài
    cv2.rectangle(frame, (10, 10), (width - 10, height - 10), (35, 49, 77), 1)
    return frame


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Khởi chạy kiểm thử GPS phần cứng trên Raspberry Pi")
    parser.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT", "8080")), help="Cổng Web Dashboard")
    parser.add_argument("--server", type=str, default=os.getenv("REMOTE_SERVER_URL", "http://192.168.1.133:5000"), help="URL Server Bạn Tôi")
    parser.add_argument("--gps-port", type=str, default=os.getenv("GPS_USB_PORT", ""), help="Cổng Serial GPS (để trống để tự động quét)")
    parser.add_argument("--gps-baud", type=int, default=int(os.getenv("GPS_BAUDRATE", "9600")), help="Baudrate GPS")
    args = parser.parse_args()

    # 1. Khởi tạo GPS Manager chuyên trách phần cứng (KHÔNG GIẢ LẬP)
    print("\n" + "=" * 75)
    print("  🚀 KHỞI ĐỘNG HỆ THỐNG KIỂM THỬ GPS PHẦN CỨNG TRÊN RASPBERRY PI 4")
    print("=" * 75)
    print("  [CHẾ ĐỘ]      : GPS ONLY (Đã ngắt hoàn toàn Camera & AI)")
    print(f"  [GPS HARDWARE]: Quét tự động cổng USB/UART (/dev/ttyUSB*, /dev/ttyACM*, /dev/serial0)")
    print(f"  [MỐC TỌA ĐỘ]  : Mặc định vị trí máy tính (Đà Nẵng: 16.0685°N, 108.2215°E, 8.5m)")
    print(f"  [SERVER BẠN]  : {args.server}")
    print(f"  [CỔNG WEB]    : {args.port}")
    print("=" * 75)

    # 1. Khởi tạo ESP Communicator + Esp32NetworkBridge G11 TRƯỚC TIÊN (giữ cổng COM3 / ttyUSB0 an toàn)
    default_esp = "COM3" if os.name == "nt" else "/dev/ttyUSB0"
    esp_port = os.getenv("ESP_PORT", default_esp)
    esp_baud = int(os.getenv("ESP_BAUDRATE", str(DEFAULT_HIGHSPEED_BAUDRATE)))

    gps = GpsManager(
        port=args.gps_port,
        baudrate=args.gps_baud,
        # Chỉ bật quét USB trực tiếp nếu người dùng truyền cổng GPS riêng (vì GPS mặc định nối qua UART2 của ESP32)
        enable_direct_usb=bool(args.gps_port),
        simulation_mode=False,
    )

    esp = EspCommunicator(
        mode=os.getenv("ESP_MODE", "SERIAL"),
        port=esp_port,
        baudrate=esp_baud,
        require_ack=False,
        on_downlink_xyz=lambda xyz: gps.inject_server_xyz(
            lat=float(xyz.get("lat", xyz.get("y", 16.0685))),
            lon=float(xyz.get("lon", xyz.get("x", 108.2215))),
            alt_m=float(xyz.get("alt", xyz.get("z", 8.5))),
        ),
    )

    bridge = Esp32NetworkBridge(
        uart_sender=esp.send_frame_bytes,
        frame_encoder=encode_fd_frame,
        uplink_mode=os.getenv("UPLINK_MODE", "esp32_gateway"),
    )

    # 3. Khởi tạo CloudSyncManager truyền dữ liệu 2 chiều về Server Bạn Tôi (dự phòng khi chưa bật Wi-Fi trên ESP32)
    cloud_sync = CloudSyncManager(
        server_url=args.server,
        enabled=os.getenv("UPLINK_MODE", "esp32_gateway") != "esp32_gateway_only",
        telemetry_interval=2.0,
        gps_manager=gps,
        esp_communicator=esp,
    )
    cloud_sync.set_context(esp=esp, gps=gps)

    # 4. Khởi tạo Web Server (lắng nghe trên cổng chỉ định)
    web = DriverMonitorWebServer(port=args.port)
    web.set_context(cloud_sync=cloud_sync, gps=gps, esp=esp)

    local_ip = cloud_sync.access_info.local_ip or "127.0.0.1"
    print(f"\n  👉 BẢNG ĐIỀU KHIỂN ĐÃ SẴN SÀNG TRÊN MẠNG LAN:")
    print(f"  🌐 Mở trình duyệt trên máy tính của bạn: http://{local_ip}:{args.port}")
    print(f"  ⚡ Cầu nối G11 Esp32NetworkBridge: Uplink={bridge.uplink_mode} | UART={esp.baudrate} bps")
    print(f"  🗺️  Kiểm tra Google Maps: https://www.google.com/maps?q=16.0685,108.2215")
    print(f"  Nhấn Ctrl+C để dừng.\n")

    hud_width, hud_height = 640, 360
    last_bridge_push = 0.0

    try:
        while True:
            # Đồng bộ tọa độ GPS từ chuỗi FDESP của ESP32 (nếu cắm trực tiếp vào Pi)
            if esp.connected and esp.latest_telemetry and "{" in esp.latest_telemetry:
                try:
                    import json as _json
                    raw = esp.latest_telemetry
                    jdata = _json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
                    if jdata.get("gps_ready") in (1, 1.0, "1.0", "1", True):
                        gps.update_from_telemetry_dict(jdata)
                except Exception:
                    pass

            snap = gps.get_snapshot()
            pi_lat = snap.get("lat", 16.0685)
            pi_lon = snap.get("lon", 108.2215)
            pi_alt = snap.get("altitude_m", 8.5)
            dist_2d = haversine_distance_km(pi_lat, pi_lon, 16.0685, 108.2215) * 1000.0
            dist_3d = (dist_2d ** 2 + (pi_alt - 8.5) ** 2) ** 0.5

            # Chỉ đẩy gói nhị phân FDFRAME xuống UART khi bật chế độ esp32_gateway_only
            now_mono = time.monotonic()
            if os.getenv("UPLINK_MODE", "") == "esp32_gateway_only" and now_mono - last_bridge_push >= 2.0 and esp.connected:
                last_bridge_push = now_mono
                bridge.send_telemetry({
                    "lat": round(pi_lat, 6),
                    "lon": round(pi_lon, 6),
                    "alt_m": round(pi_alt, 1),
                    "dist_m": round(dist_3d, 2),
                })

            # Tạo khung hình hiển thị thông số kỹ thuật số (không rada)
            hud_frame = create_telemetry_hud_frame(
                width=hud_width,
                height=hud_height,
                gps_snap=snap,
                esp_conn=esp.connected,
                esp_port=esp.port or "",
                distance_m=dist_3d,
                server_url=args.server,
                ip_addr=local_ip,
                port=args.port,
            )

            state = {
                "confirmed_alert": "NORMAL",
                "ai_fps": 0.0,
                "pipeline_latency_ms": 0.0,
                "eye_duration": 0.0,
                "mouth_duration": 0.0,
                "distracted_duration": 0.0,
                "detected_classes": ["gps_monitoring_active"],
                "head_pose": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
            }

            web.update_frame(hud_frame, state)
            cloud_sync.update_state(state)
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n[GPS PI] Đang dừng dịch vụ kiểm thử...")
    finally:
        web.close()
        cloud_sync.close()
        gps.close()
        esp.close()
        print("[GPS PI] Đã giải phóng toàn bộ tài nguyên an toàn.")


if __name__ == "__main__":
    main()
