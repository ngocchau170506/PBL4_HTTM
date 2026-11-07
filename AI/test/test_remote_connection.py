"""Script kiểm thử toàn diện mô hình kết nối Máy Chủ Xe -> Server Bạn Tôi.

Kiểm tra:
1. Đóng gói đầy đủ dữ liệu: esp, cam, địa chỉ truy cập, gps, MQ-135, Loa, vi phạm, lịch sử cảnh báo.
2. Gửi Heartbeat / Telemetry tới server bạn tôi qua HTTP POST.
3. Gửi sự kiện vi phạm thời gian thực kèm ảnh bằng chứng JPEG Base64.
4. Kiểm tra cơ chế đệm gửi bù (Offline Store-and-Forward) khi server mất kết nối.
"""
from __future__ import annotations

import json
import numpy as np
import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
from pathlib import Path
import threading
import time
import urllib.request

repo_root = Path(__file__).resolve().parents[2]
server_root = repo_root.parent / "Server"
for p in (str(repo_root), str(repo_root / "AI"), str(repo_root / "src"), str(server_root)):
    if p not in sys.path:
        sys.path.insert(0, p)

from AI.scripts.cloud_sync import CloudSyncManager
from server import FriendServerHandler, ThreadingHTTPServer, CONNECTED_VEHICLES, VIOLATION_HISTORY
import cv2


def run_tests():
    print("=" * 65)
    print("BAT DAU KIEM THU MO HINH KET NOI CLIENT -> SERVER BAN TOI")
    print("=" * 65)

    test_port = 5099
    server = ThreadingHTTPServer(("127.0.0.1", test_port), FriendServerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"[TEST SETUP] Da khoi chay Server Ban Toi tai http://127.0.0.1:{test_port}")

    # Khoi tao CloudSyncManager tro den test_port
    sync = CloudSyncManager(
        server_url=f"http://127.0.0.1:{test_port}",
        enabled=True,
        telemetry_interval=1.0,
    )

    # 1. Kiem tra dong goi va gui Telemetry
    print("\n--- BƯỚC 1: KIỂM TRA ĐÓNG GÓI & GỬI TELEMETRY TỔNG HỢP ---")
    mock_state = {
        "confirmed_alert": "DROWSY",
        "ai_fps": 24.8,
        "pipeline_latency_ms": 14.2,
        "eye_duration": 2.1,
        "mouth_duration": 0.0,
        "distracted_duration": 0.0,
        "head_pose": {"pitch": 4.5, "yaw": -2.1, "roll": 0.8},
        "detected_classes": ["closed_eye", "drowsy"],
    }
    sync.update_state(mock_state)

    payload = sync.build_telemetry_payload()
    print("[TELEMETRY PAYLOAD]:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    # Kiem tra cac truong bat buoc theo yeu cau cua nguoi dung:
    assert "esp" in payload, "Thieu thong tin esp"
    assert "camera" in payload, "Thieu thong tin camera"
    assert "access_info" in payload, "Thieu thong tin dia chi truy cap"
    assert "local_ip" in payload["access_info"], "Thieu local_ip"
    assert "stream_url" in payload["access_info"], "Thieu stream_url"
    assert "gps" in payload, "Thieu thong tin gps"
    assert "mq135" in payload, "Thieu thong tin mq135"
    assert "speaker" in payload, "Thieu thong tin Loa / speaker"
    assert "loa" in payload, "Thieu thong tin Loa (chuan G10)"
    assert "pi" in payload, "Thieu thong tin Pi (chuan G10)"
    assert "driver_state" in payload, "Thieu thong tin trang thai lai xe"
    assert "x" in payload["gps"] and "y" in payload["gps"] and "z" in payload["gps"], "Thieu truc toa do 3D XYZ trong GPS"

    ok, resp = sync._http_post("/api/v1/telemetry", payload)
    assert ok, f"Gui telemetry that bai: {resp}"
    print(f"-> Gui Telemetry thanh cong! Server phan hoi: {resp}")
    assert "XE-01" in CONNECTED_VEHICLES, "Server chua luu xe XE-01"

    # 2. Kiem tra gui Su Kien Vi Pham kem anh bang chung
    print("\n--- BƯỚC 2: KIỂM TRA GỬI SỰ KIỆN VI PHẠM KÈM ẢNH BẰNG CHỨNG ---")
    # Tao 1 anh dummy (gia lap camera chup tai xe ngu gat)
    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(dummy_frame, "EVIDENCE: DROWSY TEST", (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)

    sync.dispatch_violation(
        alert_type="DROWSY",
        frame=dummy_frame,
        started_at=time.time() - 2.5,
        confirmed_at=time.time(),
        duration_seconds=2.5,
        confidence=0.96,
        meta={"ear": 0.16, "mar": 0.08},
    )

    # Doi worker day goi tin
    time.sleep(0.6)
    assert len(VIOLATION_HISTORY) > 0, "Server chua nhan duoc vi pham"
    last_v = VIOLATION_HISTORY[-1]
    print(f"-> Server da nhan su kien: {last_v['alert_type']} tu xe {last_v['vehicle_id']}")
    print(f"   Thoi luong: {last_v['duration_seconds']}s | GPS: {last_v['gps']}")
    print(f"   MQ-135: {last_v['mq135']} | Loa phat: {last_v['speaker_alert']}")
    assert last_v["evidence_image"].startswith("data:image/jpeg;base64,"), "Anh bang chung khong phai base64 hop le"
    print(f"   Do dai anh Base64: {len(last_v['evidence_image'])} ky tu")

    # 3. Kiem tra che do Spool / Gui bu khi mat mang
    print("\n--- BƯỚC 3: KIỂM TRA CHỐNG MẤT DỮ LIỆU KHI MẤT MẠNG (OFFLINE SPOOL) ---")
    # Tam tat server de gia lap mat song 4G
    server.shutdown()
    server.server_close()
    print("[MANG] Da ngat ket noi toi server ban toi (gia lap xe di qua ham / mat 4G)")

    # Gui them 1 vi pham luc mat mang
    sync.dispatch_violation(
        alert_type="YAWNING",
        frame=dummy_frame,
        duration_seconds=1.8,
        confidence=0.91,
    )

    # Cho worker thu gui va dem vao SQLite (toi da 4s)
    spool_ok = False
    for _ in range(20):
        time.sleep(0.2)
        stats = sync.get_sync_stats()
        if stats["pending_spool"] >= 1:
            spool_ok = True
            break

    stats = sync.get_sync_stats()
    print(f"-> Trang thai dong bo: {stats['last_sync_status']}")
    print(f"   So goi tin dang dem trong SQLite cho gui bu: {stats['pending_spool']}")
    assert spool_ok, "Goi tin chua duoc luu vao hang doi dem SQLite!"

    # 4. Kiem tra Tu Dong Bat Tay & Xa Sach Hang Doi Dem khi co mang tro lai (FEAT-112)
    print("\n--- BƯỚC 4: KIỂM TRA TỰ ĐỘNG BẮT TAY & XẢ SẠCH ĐỆM KHI CÓ MẠNG (FEAT-112) ---")
    server2 = ThreadingHTTPServer(("127.0.0.1", test_port), FriendServerHandler)
    server2_thread = threading.Thread(target=server2.serve_forever, daemon=True)
    server2_thread.start()
    print(f"[MANG] Server Ban Toi da online tro lai tai http://127.0.0.1:{test_port}")

    # Kích hoạt nhịp telemetry và xả đệm
    drained_ok = False
    for _ in range(25):
        time.sleep(0.3)
        sync._drain_spool()
        stats = sync.get_sync_stats()
        if stats["pending_spool"] == 0:
            drained_ok = True
            break

    stats = sync.get_sync_stats()
    print(f"-> Hang doi dem SQLite sau khi xa: {stats['pending_spool']} goi tin")
    print(f"-> Tong so vi pham da gui thanh cong: {stats['violations_sent']}")
    assert drained_ok, f"Hang doi dem chua duoc xa sach sau khi co mang! (Con lai: {stats['pending_spool']})"
    assert any(v.get("alert_type") == "YAWNING" for v in VIOLATION_HISTORY), "Server chua nhan duoc goi tin YAWNING xa tu hang doi dem!"
    print("  [PASS] Tu dong bat tay & xa dem SQLite hoan tat 100%!")

    # 5. Kiem tra Bat Tay Vien Thong Hai Chieu & Nap Toa Do Server XYZ (FEAT-113)
    print("\n--- BƯỚC 5: KIỂM TRA BẮT TAY HAI CHIỀU & NẠP TỌA ĐỘ SERVER-ASSISTED XYZ ---")
    # Server Ban Toi phat toa do deo Da Lat (Alt 1500m)
    inject_req = urllib.request.Request(
        f"http://127.0.0.1:{test_port}/api/v1/geo/inject_xyz",
        data=json.dumps({"lat": 11.9404, "lon": 108.4583, "alt_m": 1500.0, "speed_kmh": 28.5}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(inject_req, timeout=2.0) as resp:
        assert resp.status == 200, "Inject XYZ that bai"

    # Xe gui 1 nhip telemetry, server se dong goi downlink_xyz vao ACK
    ok, ack_resp = sync._http_post("/api/v1/telemetry", sync.build_telemetry_payload())
    assert ok, f"Gui telemetry that bai: {ack_resp}"
    time.sleep(0.1)

    gps_snap = sync.gps_manager.get_snapshot()
    print(f"-> Snapshot GPS sau khi nap Server XYZ:")
    print(f"   X(Lon): {gps_snap['x']} | Y(Lat): {gps_snap['y']} | Z(Alt): {gps_snap['z']}m | Nguon: {gps_snap['source']}")
    assert gps_snap["source"] == "server_xyz", f"Nguon toa do khong phai server_xyz: {gps_snap['source']}"
    assert abs(gps_snap["lat"] - 11.9404) < 1e-4, f"Lat sai: {gps_snap['lat']}"
    assert abs(gps_snap["lon"] - 108.4583) < 1e-4, f"Lon sai: {gps_snap['lon']}"
    assert abs(gps_snap["altitude_m"] - 1500.0) < 1.0, f"Alt sai: {gps_snap['altitude_m']}"
    assert gps_snap["x"] == gps_snap["lon"] and gps_snap["y"] == gps_snap["lat"] and gps_snap["z"] == gps_snap["altitude_m"]
    print("  [PASS] Bắt tay 2 chiều và nạp Downlink 3D XYZ thành công chuẩn xác!")

    # 6. Kiem tra duong truyen vien thong chuan G10 / FEAT-102 (TelemetryStreamer & Multiplexer)
    print("\n--- BƯỚC 6: KIỂM TRA STREAMING VIỄN THÔNG 5 NHÓM CẢM BIẾN (FEAT-101 & FEAT-102) ---")
    from fd_response.telemetry_multiplexer import TelemetryMultiplexer, EspTelemetry, Mq135Telemetry, GpsTelemetry, LoaTelemetry, PiTelemetry
    from fd_response.telemetry_streamer import TelemetryStreamer

    downlink_received_box = []
    streamer = TelemetryStreamer(
        endpoint_url=f"http://127.0.0.1:{test_port}/api/v1/telemetry",
        on_downlink_xyz=lambda dl: downlink_received_box.append(dl)
    )
    mux = TelemetryMultiplexer(device_id="XE-01-PI4")
    mux.update_esp(seq=201, vibration_g=0.035, adc_raw=3.3, is_connected=True)
    mux.update_mq135(co2_ppm=510.0, mq135_raw=320.0, mq135_ppm=510.0, air_quality_level="GOOD")
    mux.update_gps(lat=11.9404, lon=108.4583, alt_m=1500.0, speed_kmh=28.5, source="server_xyz")
    mux.update_loa(alert_level="NORMAL", alert_code="NORMAL", buzzer_active=False, volume_pct=90)
    mux.update_pi(cpu_temp_c=45.2, ram_used_mb=68.5, cpu_usage_pct=15.4, camera_fps=25.0, ai_status="MONITORING")

    packet = mux.pack(trace_id="TR-TEST-102")
    ok, msg, dl = streamer.send(packet)
    assert ok, f"Streamer send that bai: {msg}"
    assert streamer.is_connected, "Streamer chua chuyen sang trang thai Connected"
    assert len(downlink_received_box) > 0, "Downlink callback chua duoc kich hoat"
    print(f"-> Streamer gui thanh cong 5 nhom tin hieu: esp, mq135, gps, loa, pi")
    print(f"   Server ACK: {msg} | Downlink nhan duoc: {dl}")

    # Thu nghiem spool RAM cua Streamer khi server tam tat
    server2.shutdown()
    server2.server_close()
    ok_offline, msg_off, _ = streamer.send(packet)
    assert not ok_offline, "Streamer phai phat hien server offline"
    assert streamer.spool_size == 1, "Streamer chua dem goi tin vao RAM deque"
    print(f"-> Streamer offline spool: dem {streamer.spool_size} goi tin trong RAM khong nghen tien trinh")

    sync.close()
    print("\n" + "=" * 65)
    print("TAT CA CAC PHEP THU KET NOI CLIENT -> SERVER HOAN TOAN THANH CONG!")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
