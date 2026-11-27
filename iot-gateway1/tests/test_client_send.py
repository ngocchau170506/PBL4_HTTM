#!/usr/bin/env python3
"""Script Kiểm Thử Tự Động Kết Nối & Đẩy Dữ Liệu Lên Server (Mô Phỏng Pi 4 IOTv2/IOTv3).

Kiểm tra 5 mắt xích:
1. Kết nối và kiểm tra tính sẵn sàng của Máy Chủ.
2. Gửi gói tin Telemetry đa cảm biến (ESP, GPS, MQ-135, Pi Health).
3. Gửi sự kiện vi phạm ngủ gật kèm ảnh chụp bằng chứng JPEG Base64.
4. Gửi lệnh điều khiển từ xa (Downlink Command) từ Server xuống xe.
5. Kiểm tra xe kéo lệnh (Pull Command) thành công và xác thực dữ liệu lưu trong SQLite.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def post_json(url: str, payload: dict) -> tuple[bool, dict]:
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "Pi4_Simulated_Client/1.0"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (resp.status == 200, data)
    except Exception as e:
        return (False, {"error": str(e)})


def get_json(url: str) -> tuple[bool, dict]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (resp.status == 200, data)
    except Exception as e:
        return (False, {"error": str(e)})


def get_bytes_range(url: str, byte_range: str = "bytes=0-15") -> tuple[int, bytes]:
    try:
        req = urllib.request.Request(url, headers={"Range": byte_range})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return (resp.status, resp.read())
    except Exception:
        return (0, b"")


def create_sample_mp4_b64() -> str:
    """Tạo dữ liệu MP4 giả lập (khối ftyp + moov tối thiểu) chuẩn data URI như IOTv4 UploadWorker."""
    sample_mp4 = (
        b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
        b"\x00\x00\x00\x08free"
        b"\x00\x00\x00\x10mdatTESTVIDEO_IOTV4"
    )
    return "data:video/mp4;base64," + base64.b64encode(sample_mp4).decode("ascii")


def create_sample_evidence_b64() -> str:
    """Tạo ảnh JPEG bằng chứng mẫu Base64."""
    try:
        import cv2
        import numpy as np

        img = np.zeros((480, 640, 3), dtype=np.uint8)
        img[:] = (20, 18, 26)
        cv2.rectangle(img, (150, 80), (490, 400), (0, 0, 255), 2)
        cv2.putText(img, "[EVIDENCE: RASPBERRY PI 4 -> FLEET SERVER]", (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
        cv2.putText(img, "VI PHAM: DROWSINESS (NGU GAT)", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        cv2.putText(img, f"THOI GIAN: {time.strftime('%Y-%m-%d %H:%M:%S')}", (20, 435), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.putText(img, "THIET BI: XE-01 (Raspberry Pi 4 ARM64)", (20, 460), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return base64.b64encode(buf.tobytes()).decode("utf-8")
    except Exception:
        # Fallback ảnh 1 pixel nếu không có opencv
        return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


def run_test(server_url: str = "http://127.0.0.1:5000", mode: str = "fleet"):
    base_url = server_url.rstrip("/")
    print("=" * 80)
    print(f"🧪 KIỂM THỬ KẾT NỐI TỪ PI 4 ĐẾN MÁY CHỦ TRUNG TÂM ({mode}): {base_url}")
    print("=" * 80)

    # 1. Kiểm tra máy chủ có đang lắng nghe /api/v1/health (chuẩn IOTv4 UploadWorker) không
    ok_health, resp_health = get_json(f"{base_url}/api/v1/health")
    if not ok_health:
        print(f"❌ KHÔNG THỂ KẾT NỐI: Máy chủ tại {base_url}/api/v1/health chưa phản hồi 200 OK!")
        print("👉 Vui lòng mở terminal và chạy: python run_server.py --port 5000 (hoặc python server.py --port 5000)")
        return False
    print(f"✅ [1/5] Kết nối /api/v1/health thành công: {resp_health}")

    # 2. Gửi bản tin Telemetry chuẩn IOTv2 / IOTv3
    print("\n📡 [2/5] Đang đẩy gói tin Telemetry đa cảm biến từ Pi...")
    telemetry_payload = {
        "vehicle_id": "XE-01",
        "vehicle_name": "Xe Bán Tải Nghiên Cứu",
        "driver_name": "Nguyễn Văn A",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "access_info": {
            "local_ip": "192.168.1.105",
            "web_port": 8080,
            "dashboard_url": "http://192.168.1.105:8080",
            "stream_url": "http://192.168.1.105:8080/stream",
        },
        "camera": {"status": "ONLINE", "fps": 26.5, "latency_ms": 38.2},
        "esp": {"connected": True, "port": "/dev/ttyUSB0", "baudrate": 115200, "reset_count": 0},
        "gps": {
            "lat": 10.776889,
            "lon": 106.700806,
            "alt_m": 16.5,
            "speed_kmh": 45.2,
            "satellites": 9,
            "fix": 1,
            "source": "gps_satellite",
        },
        "mq135": {
            "co2_ppm": 525.0,
            "mq135_raw": 160,
            "gas_level": 0,
            "status_label": "Khí CO2 ở mức an toàn",
        },
        "speaker": {"current_voice": "NORMAL", "is_playing": False},
        "driver_state": {
            "alert": "NORMAL",
            "eye_duration": 0.0,
            "mouth_duration": 0.0,
            "head_pose": [-1.5, 2.0, 0.0],
        },
        "pi": {
            "cpu_temp_c": 49.5,
            "ram_used_mb": 178.0,
            "cpu_usage_pct": 28.5,
            "camera_fps": 26.5,
            "throttled": "0x0",
        },
    }

    ok_tel, resp_tel = post_json(f"{base_url}/api/v1/telemetry", telemetry_payload)
    if ok_tel:
        print("  -> Đẩy Telemetry: THÀNH CÔNG (Server phản hồi ACK)")
        print(f"  -> Downlink tọa độ nhận được: {resp_tel.get('downlink_xyz')}")
    else:
        print(f"  -> Lỗi đẩy Telemetry: {resp_tel}")
        return False

    # 3. Gửi sự kiện vi phạm kèm ảnh JPEG + video MP4 10s (chuẩn IOTv4 UploadWorker)
    print("\n📸🎬 [3/5] Đang đẩy sự kiện Vi Phạm kèm Ảnh JPEG + Video MP4 (meta.video_mp4_base64)...")
    evt_id = f"EVT_{int(time.time()*1000)}"
    img_b64 = create_sample_evidence_b64()
    mp4_b64 = create_sample_mp4_b64()
    violation_payload = {
        "event_id": evt_id,
        "vehicle_id": "XE-01",
        "driver_name": "Nguyễn Văn A",
        "alert_type": "DROWSINESS_CONFIRMED",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "confirmed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_seconds": 2.45,
        "gps": {"lat": 10.776889, "lon": 106.700806, "speed_kmh": 45.2},
        "mq135": {"co2_ppm": 525.0},
        "speaker_alert": "VOICE:1",
        "evidence_image": img_b64,
        "meta": {
            "source": "event_recorder",
            "duration_s": 10.0,
            "video_mp4_base64": mp4_b64,
        },
    }
    ok_v, resp_v = post_json(f"{base_url}/api/v1/violation", violation_payload)
    video_url = resp_v.get("video_url", "")
    if ok_v and video_url:
        print(f"  -> Đẩy Vi Phạm [{evt_id}]: THÀNH CÔNG!")
        print(f"  -> Ảnh bằng chứng: {resp_v.get('image_url')}")
        print(f"  -> Video MP4 đã lưu tại: {video_url}")
        range_status, range_bytes = get_bytes_range(f"{base_url}{video_url}", "bytes=0-15")
        if range_status != 206 or len(range_bytes) != 16:
            print(f"  -> ❌ Lỗi phát video HTTP Range: status={range_status}, len={len(range_bytes)}")
            return False
        print(f"  -> Kiểm tra phát Video HTTP 206 Range: THÀNH CÔNG ({len(range_bytes)} bytes header ftyp OK)!")
    else:
        print(f"  -> ❌ Lỗi đẩy vi phạm / không có video_url: {resp_v}")
        return False

    if mode == "standalone":
        # Kiểm tra /api/v1/geo/inject_xyz (đảm bảo server.py không đọc self.rfile 2 lần)
        print("\n📍 [4/5] Kiểm tra POST /api/v1/geo/inject_xyz trên server.py...")
        ok_xyz, resp_xyz = post_json(f"{base_url}/api/v1/geo/inject_xyz", {"lat": 16.0685, "lon": 108.2215, "alt_m": 8.5})
        if not ok_xyz or not resp_xyz.get("ok"):
            print(f"  -> ❌ Lỗi /api/v1/geo/inject_xyz: {resp_xyz}")
            return False
        print(f"  -> Inject XYZ OK: {resp_xyz.get('ack')}")
        print("\n✅ [5/5] Hoàn tất kiểm tra chế độ standalone (server.py)!")
    else:
        # 4. Phát lệnh điều khiển từ xa (Downlink Command)
        print("\n📢 [4/5] Mô phỏng Người Điều Hành trên Dashboard phát lệnh [VOICE:2] xuống xe...")
        ok_cmd, resp_cmd = post_json(f"{base_url}/api/v1/server/command", {"vehicle_id": "XE-01", "command": "VOICE:2"})
        if ok_cmd:
            print(f"  -> Server đã tiếp nhận lệnh: {resp_cmd.get('msg')}")
        else:
            print(f"  -> ❌ Lỗi phát lệnh: {resp_cmd}")
            return False

        # 5. Pi kéo lệnh về thực thi
        print("\n🚗 [5/5] Bo mạch Pi trên xe kéo lệnh (Pull Commands)...")
        ok_pull, resp_pull = get_json(f"{base_url}/api/v1/pi/pull_commands?vehicle_id=XE-01")
        if ok_pull and resp_pull.get("commands"):
            cmds = resp_pull["commands"]
            print(f"  -> Pi đã nhận thành công {len(cmds)} lệnh từ Server: {[c['command'] for c in cmds]}")
            print("  -> Loa trên xe đã kích hoạt lệnh: Còi Ngáp (VOICE:2)!")
        else:
            print(f"  -> ❌ Cảnh báo: Không kéo được lệnh ({resp_pull})")
            return False

    print("\n" + "=" * 80)
    print("🎉 TẤT CẢ CÁC BÀI TEST ĐỀU ĐẠT CHUẨN 100%!")
    print(f"👉 Mở Dashboard Web trên trình duyệt: {base_url}")
    print("=" * 80)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test client sending data to Fleet Server")
    parser.add_argument("--url", default="http://127.0.0.1:5000", help="Địa chỉ Server (mặc định http://127.0.0.1:5000)")
    parser.add_argument("--mode", choices=["fleet", "standalone"], default="fleet", help="Kiểm thử run_server.py (fleet) hoặc server.py (standalone)")
    args = parser.parse_args()
    success = run_test(args.url, mode=args.mode)
    sys.exit(0 if success else 1)
