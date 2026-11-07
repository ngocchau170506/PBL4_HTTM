"""Kịch bản kiểm thử toàn diện module GPS độc lập và chống lỗi (TEST-GPS).

Kiểm tra:
1. Chuyển đổi tọa độ NMEA (DDMM.MMMM -> Decimal Degrees DD.DDDDDD) chuẩn xác.
2. Quy đổi tốc độ từ Hải lý (Knots) sang km/h (nhân 1.852).
3. Bộ lọc chống trôi khi xe dừng đèn đỏ (Stationary Deadband Filter < 1.8 km/h).
4. Tính toán quãng đường di chuyển (Haversine Trip Distance / Odometer).
5. Phân tích cả 2 câu NMEA RMC (Tọa độ, tốc độ, hướng) và GGA (Vệ tinh, cao độ).
6. Tích hợp trơn tru với chuỗi telemetry ESP32 (FDESP: JSON) và CloudSyncManager.
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

# Khắc phục bảng mã console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Thêm thư mục gốc vào PYTHONPATH
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from AI.scripts.gps_service import (
    GpsManager,
    GpsPoint,
    haversine_distance_km,
    parse_nmea_coordinate,
)
from AI.scripts.cloud_sync import CloudSyncManager


def test_nmea_coordinate_conversion():
    print("\n--- 1. Kiểm tra chuyển đổi định dạng tọa độ NMEA ---")
    # Ví dụ: 1045.7573 N -> 10 + 45.7573/60 = 10.762622...
    lat = parse_nmea_coordinate("1045.7573", "N")
    expected_lat = 10.0 + (45.7573 / 60.0)
    assert abs(lat - expected_lat) < 1e-5, f"Latitude sai: {lat} != {expected_lat}"
    print(f"  [PASS] '1045.7573', 'N' -> {lat:.6f}° (Kỳ vọng: {expected_lat:.6f}°)")

    # Ví dụ: 10639.6103 E -> 106 + 39.6103/60 = 106.660172...
    lon = parse_nmea_coordinate("10639.6103", "E")
    expected_lon = 106.0 + (39.6103 / 60.0)
    assert abs(lon - expected_lon) < 1e-5, f"Longitude sai: {lon} != {expected_lon}"
    print(f"  [PASS] '10639.6103', 'E' -> {lon:.6f}° (Kỳ vọng: {expected_lon:.6f}°)")

    # Bán cầu Nam (S) & Tây (W) phải có dấu âm
    lat_s = parse_nmea_coordinate("1230.0000", "S")
    assert lat_s == -12.5, f"Nam bán cầu phải là số âm: {lat_s}"
    print(f"  [PASS] '1230.0000', 'S' -> {lat_s}° (Số âm chính xác)")

    lon_w = parse_nmea_coordinate("07045.0000", "W")
    assert lon_w == -70.75, f"Tây bán cầu phải là số âm: {lon_w}"
    print(f"  [PASS] '07045.0000', 'W' -> {lon_w}° (Số âm chính xác)")

    # Dữ liệu hỏng / rác không được ném Exception
    assert parse_nmea_coordinate("", "N") == 0.0
    assert parse_nmea_coordinate("INVALID", "N") == 0.0
    print("  [PASS] Xử lý chuỗi rác an toàn (trả về 0.0 không crash)")


def test_haversine_distance():
    print("\n--- 2. Kiểm tra tính khoảng cách mặt đất (Haversine) ---")
    # Tọa độ A: Chợ Bến Thành (10.772500, 106.698000)
    # Tọa độ B: Landmark 81 (10.795000, 106.721800)
    # Khoảng cách chim bay khoảng 3.65 - 3.75 km
    dist_km = haversine_distance_km(10.772500, 106.698000, 10.795000, 106.721800)
    print(f"  [PASS] Khoảng cách Bến Thành -> Landmark 81: {dist_km:.3f} km")
    assert 3.5 <= dist_km <= 4.0, f"Khoảng cách Haversine bất thường: {dist_km} km"


def test_nmea_sentence_parsing():
    print("\n--- 3. Kiểm tra phân tích câu NMEA RMC & GGA ---")
    mgr = GpsManager(simulation_mode=False)

    # 1. Câu $GPRMC: Tọa độ, tốc độ (15 knots), hướng (120 độ)
    # 15 knots * 1.852 = 27.78 km/h -> round(1) = 27.8 km/h
    rmc_line = "$GPRMC,083559.00,A,1045.7573,N,10639.6103,E,15.0,120.0,311026,,,A*60"
    ok = mgr.parse_nmea_sentence(rmc_line)
    assert ok, "Không phân tích được câu $GPRMC"

    snap = mgr.get_snapshot()
    assert snap["is_valid"] is True
    assert snap["speed_kmh"] == 27.8, f"Tốc độ quy đổi sai: {snap['speed_kmh']} != 27.8 km/h"
    assert snap["heading_deg"] == 120.0
    assert abs(snap["lat"] - 10.762622) < 1e-4
    assert abs(snap["lon"] - 106.660172) < 1e-4
    print(f"  [PASS] $GPRMC: Tốc độ {snap['speed_kmh']} km/h (15 knots * 1.852), Hướng {snap['heading_deg']}°")

    # 2. Câu $GPGGA: Số vệ tinh (9), Cao độ (14.5 mét), Chất lượng (1)
    gga_line = "$GPGGA,083559.00,1045.7573,N,10639.6103,E,1,09,1.1,14.5,M,-2.5,M,,*5C"
    ok = mgr.parse_nmea_sentence(gga_line)
    assert ok, "Không phân tích được câu $GPGGA"

    snap = mgr.get_snapshot()
    assert snap["satellites"] == 9, f"Số vệ tinh sai: {snap['satellites']}"
    assert snap["altitude_m"] == 14.5, f"Cao độ sai: {snap['altitude_m']}"
    print(f"  [PASS] $GPGGA: Vệ tinh = {snap['satellites']}, Cao độ = {snap['altitude_m']} m")


def test_stationary_deadband_filter():
    print("\n--- 4. Kiểm tra Bộ Lọc Chống Trôi Khi Dừng Xe (Deadband Filter < 1.8 km/h) ---")
    mgr = GpsManager(deadband_speed_kmh=1.8, simulation_mode=False)

    # Khởi tạo vị trí ban đầu xe đang chạy 30 km/h
    mgr.update_from_telemetry_dict({
        "gps_lat": 10.762622,
        "gps_lon": 106.660172,
        "speed_kmh": 30.0,
        "gps_ready": "1.0",
        "sats": 8,
    })
    snap1 = mgr.get_snapshot()
    assert snap1["speed_kmh"] == 30.0
    print(f"  [PASS] Xe đang di chuyển: Tốc độ {snap1['speed_kmh']} km/h")

    # Giờ xe dừng đèn đỏ: GPS module bị nhiễu sinh ra tốc độ ảo 0.8 km/h và tọa độ hơi nhảy
    mgr.update_from_telemetry_dict({
        "gps_lat": 10.762680,  # Nhảy nhẹ vài mét do nhiễu
        "gps_lon": 106.660210,
        "speed_kmh": 0.8,      # Nhiễu < 1.8 km/h
        "gps_ready": "1.0",
        "sats": 8,
    })
    snap2 = mgr.get_snapshot()
    # Deadband filter phải ép tốc độ về 0.0 và giữ nguyên tọa độ cũ để không trôi bản đồ
    assert snap2["speed_kmh"] == 0.0, f"Deadband thất bại: Tốc độ không bị triệt tiêu: {snap2['speed_kmh']}"
    assert snap2["lat"] == snap1["lat"], "Deadband thất bại: Tọa độ bị trôi khi xe dừng đèn đỏ"
    assert snap2["lon"] == snap1["lon"], "Deadband thất bại: Tọa độ bị trôi khi xe dừng đèn đỏ"
    print(f"  [PASS] Deadband hoạt động hoàn hảo: Nhiễu 0.8 km/h -> Ép về {snap2['speed_kmh']} km/h, Tọa độ giữ khóa cố định!")


def test_trip_odometer_accumulation():
    print("\n--- 5. Kiểm tra Tích Lũy Quãng Đường Xe Chạy (Trip Odometer) ---")
    mgr = GpsManager(simulation_mode=False)

    # Điểm 1: Tọa độ ban đầu
    mgr.update_from_telemetry_dict({
        "gps_lat": 10.772500,
        "gps_lon": 106.698000,
        "speed_kmh": 40.0,
        "gps_ready": "1.0",
    })
    assert mgr.get_snapshot()["trip_km"] == 0.0

    # Điểm 2: Xe di chuyển cách khoảng 250 mét (~0.002 độ)
    mgr.update_from_telemetry_dict({
        "gps_lat": 10.774500,
        "gps_lon": 106.699500,
        "speed_kmh": 40.0,
        "gps_ready": "1.0",
    })
    trip_km = mgr.get_snapshot()["trip_km"]
    assert trip_km > 0.1, f"Trip odometer không tích lũy: {trip_km} km"
    print(f"  [PASS] Odometer tích lũy chuẩn xác: {trip_km} km sau 1 chặng di chuyển")


def test_cloud_sync_integration():
    print("\n--- 6. Kiểm tra tích hợp GPS với CloudSyncManager ---")
    test_db = WORKSPACE_ROOT / "AI" / "data" / "test_gps_sync.db"
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass

    # Giả lập ESP Communicator gửi chuỗi JSON FDESP:
    class MockEsp:
        connected = True
        port = "COM3"
        baudrate = 115200
        reset_count = 0
        latest_telemetry = 'FDESP:{"seq":101,"co2_ppm":460.5,"co2_level":0,"gps_ready":"1.0","gps_lat":10.762622,"gps_lon":106.660172,"speed_kmh":52.4,"heading":135.0,"altitude":12.0,"sats":9}'

    sync = CloudSyncManager(
        server_url="http://127.0.0.1:5000",
        enabled=False,  # Không cần gửi HTTP thật, chỉ kiểm tra payload
        db_path=str(test_db),
    )
    sync.set_context(esp=MockEsp())

    payload = sync.build_telemetry_payload()
    gps_info = payload.get("gps", {})

    assert gps_info.get("lat") == 10.762622
    assert gps_info.get("lon") == 106.660172
    assert gps_info.get("speed_kmh") == 52.4
    assert gps_info.get("heading_deg") == 135.0
    assert gps_info.get("altitude_m") == 12.0
    assert gps_info.get("satellites") == 9
    assert gps_info.get("is_valid") is True
    assert "https://www.google.com/maps?q=" in gps_info.get("google_maps_url", "")

    print("  [PASS] CloudSync telemetry payload đóng gói đầy đủ các thông số GPS:")
    for k, v in gps_info.items():
        print(f"         - {k}: {v}")

    sync.close()
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass


def main():
    print("======================================================================")
    print("      KIỂM THỬ HỆ THỐNG ĐỊNH VỊ GPS VÀ CHỐNG LỖI VẶT (TEST-GPS)       ")
    print("======================================================================")
    start_time = time.time()

    test_nmea_coordinate_conversion()
    test_haversine_distance()
    test_nmea_sentence_parsing()
    test_stationary_deadband_filter()
    test_trip_odometer_accumulation()
    test_cloud_sync_integration()

    elapsed = time.time() - start_time
    print("\n======================================================================")
    print(f"🎉 TẤT CẢ 6 HẠNG MỤC KIỂM THỬ GPS ĐỀU ĐẠT CHUẨN XUẤT SẮC! ({elapsed:.2f}s)")
    print("======================================================================")


if __name__ == "__main__":
    main()
