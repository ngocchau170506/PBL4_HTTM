#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KỊCH BẢN DEMO KẾT NỐI: IOT-GATEWAY -> BACKEND -> FRONTEND (PBL4_HTTM)
====================================================================
Dùng để trình chiếu trực tiếp cho giảng viên thấy:
1. Gói tin từ thiết bị Edge gửi vào IoT Gateway (:5000)
2. IoT Gateway (server_ai) tự động chuẩn hóa và đẩy (bridge) sang Backend (:3000)
3. Backend lưu Database Postgres và phát Socket.IO thời gian thực
4. Frontend React (:5173) lập tức cập nhật bản đồ GPS, chuông báo động đỏ và trừ điểm uy tín tài xế!
"""

import sys
import time
import json
import urllib.request
import urllib.error

GATEWAY_URL = "http://127.0.0.1:5000"
DEVICE_TOKEN = "de37103895a154174ee1b4425ab85a24710a02f770791961"

def print_step(title):
    print("\n" + "=" * 65)
    print(f"👉 {title}")
    print("=" * 65)

def send_request(endpoint, payload):
    url = f"{GATEWAY_URL}{endpoint}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {DEVICE_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "PBL4_Demo_Client/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            res_body = resp.read().decode("utf-8")
            return resp.status, json.loads(res_body)
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        return e.code, err_msg
    except Exception as e:
        return 0, str(e)

def demo_telemetry_normal():
    print_step("KỊCH BẢN 1: Gửi Telemetry GPS bình thường (Xe 43C-123.45 chạy trên đường)")
    payload = {
        "vehicle_id": "XE-01",
        "driver_name": "Lê Minh Tuấn",
        "gps": {
            "lat": 16.0544,
            "lon": 108.2022,
            "speed_kmh": 48.5,
            "is_valid": True,
        },
        "mq135": {
            "co2_ppm": 450.0,
        },
        "pi": {
            "cpu_temp_c": 52.0,
        },
    }
    print(f"📡 Đang gửi Telemetry -> {GATEWAY_URL}/api/v1/telemetry ...")
    status, res = send_request("/api/v1/telemetry", payload)
    print(f"   [Cổng 5000 phản hồi]: HTTP {status}")
    if status == 200:
        print("   ✅ Thành công! IoT Gateway đã nhận gói tin và chuyển tiếp sang Backend (:3000).")
        print("   👀 HÃY NHÌN TRÊN FRONTEND (:5173): Xe 43C-123.45 cập nhật vị trí GPS trên Bản đồ, Marker XANH AN TOÀN.")
    else:
        print(f"   ❌ Thất bại: {res}")

def demo_telemetry_danger_co2():
    print_step("KỊCH BẢN 2: Cảnh báo nồng độ khí CO2 khoang lái cao (> 1200 ppm)")
    payload = {
        "vehicle_id": "XE-01",
        "driver_name": "Lê Minh Tuấn",
        "gps": {
            "lat": 16.0680,
            "lon": 108.2150,
            "speed_kmh": 62.0,
            "is_valid": True,
        },
        "mq135": {
            "co2_ppm": 1380.0,  # Nguy cấp
        },
        "pi": {
            "cpu_temp_c": 58.5,
        },
    }
    print(f"📡 Đang gửi Telemetry (CO2 cao: 1380 ppm) -> {GATEWAY_URL}/api/v1/telemetry ...")
    status, res = send_request("/api/v1/telemetry", payload)
    print(f"   [Cổng 5000 phản hồi]: HTTP {status}")
    if status == 200:
        print("   ✅ Thành công! IoT Gateway đã chuyển tiếp sang Backend.")
        print("   👀 HÃY NHÌN TRÊN FRONTEND (:5173): Marker xe trên bản đồ đổi màu ĐỎ CẢNH BÁO (DANGER)!")
    else:
        print(f"   ❌ Thất bại: {res}")

def demo_violation_microsleep():
    print_step("KỊCH BẢN 3: Phát hiện AI Vi phạm ngủ gật (Microsleep - Mắt nhắm >= 2s)")
    event_id = f"DEMO_BURST_{int(time.time())}"
    
    # 1. Gửi trực tiếp sang backend qua bridge endpoint hoặc vi phạm test
    # Lưu ý: server_ai có thể nhận burst ảnh hoặc gửi trực tiếp vi phạm xác thực
    # Ta gửi vi phạm giả lập trực tiếp qua Backend endpoint /api/v1/edge/violations
    # để chắc chắn Socket.io bắn ngay lập tức
    url = "http://127.0.0.1:3000/api/v1/edge/violations"
    body = {
        "trip_id": 2,
        "ma_loai": "MICROSLEEP",
        "ai_confidence_score": 0.96,
        "mo_ta": "Mô hình AI YOLO phát hiện mắt tài xế nhắm liên tục 2.4s khi xe đang chạy 50 km/h.",
        "client_event_id": event_id,
        "video_url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Idempotency-Key": event_id,
            "User-Agent": "PBL4_Bridge_Demo/1.0",
        },
        method="POST",
    )
    print(f"🚨 Đang kích hoạt vi phạm AI -> Backend http://127.0.0.1:3000/api/v1/edge/violations ...")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            print(f"   [Backend phản hồi]: HTTP {resp.status} - Ghi nhận vi phạm thành công!")
            v = res.get("data", {})
            print(f"   - Loại vi phạm: {v.get('tenLoai')}")
            print(f"   - Điểm trừ uy tín: -{v.get('diemTru')} điểm")
            print(f"   - Tài xế: {v.get('driver', {}).get('user', {}).get('hoTen')} (Điểm uy tín mới: {v.get('driver', {}).get('uyTin')} điểm)")
            print("   👀 HÃY NHÌN TRÊN FRONTEND (:5173):")
            print("      1. Chuông Socket Alert đỏ rực hiện ở góc màn hình thông báo vi phạm.")
            print("      2. Tab 'Nhật ký vi phạm' (Logs) xuất hiện bản ghi kèm Video bằng chứng & Trạm dừng chân gợi ý!")
    except Exception as e:
        print(f"   ❌ Thất bại: {e}")

def main():
    print("""
=====================================================================
  CHƯƠNG TRÌNH DEMO KẾT NỐI IOT GATEWAY -> BACKEND -> FRONTEND
=====================================================================
    """)
    while True:
        print("\nChọn kịch bản demo muốn trình chiếu:")
        print("  1. Demo Telemetry bình thường (Cập nhật vị trí GPS & CO2 xanh)")
        print("  2. Demo Telemetry cảnh báo (Nồng độ CO2 cao 1380 ppm -> Marker đỏ)")
        print("  3. Demo Báo động AI vi phạm ngủ gật (Microsleep -> Chuông đỏ + Trừ điểm + Video)")
        print("  4. Chạy toàn bộ cả 3 kịch bản liên tiếp (Khuyên dùng khi thuyết trình)")
        print("  0. Thoát")
        
        choice = input("\nNhập lựa chọn của bạn (1-4, 0): ").strip()
        if choice == "1":
            demo_telemetry_normal()
        elif choice == "2":
            demo_telemetry_danger_co2()
        elif choice == "3":
            demo_violation_microsleep()
        elif choice == "4":
            demo_telemetry_normal()
            print("\n⏳ Đang đợi 3 giây trước khi phát kịch bản tiếp theo...")
            time.sleep(3)
            demo_telemetry_danger_co2()
            print("\n⏳ Đang đợi 3 giây trước khi phát kịch bản tiếp theo...")
            time.sleep(3)
            demo_violation_microsleep()
            print("\n🎉 HOÀN THÀNH TOÀN BỘ KỊCH BẢN DEMO!")
        elif choice == "0":
            print("Tạm biệt!")
            break
        else:
            print("Lựa chọn không hợp lệ, vui lòng chọn lại.")

if __name__ == "__main__":
    main()
