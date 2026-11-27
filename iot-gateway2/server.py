"""Server Trung Tâm Độc Lập (PC Host) — Tự Động Kiểm Tra Môi Trường & Kết Nối Trước Khi Chạy.

Thư mục độc lập: E:\\AI\\Server\\server.py
Chạy trực tiếp trên Máy tính (PC):
    python E:\\AI\\Server\\server.py --pi-ip 192.168.1.7 --port 5000

Quy trình tự động khi khởi chạy (Bước 4):
1. KIỂM TRA MÔI TRƯỜNG MÁY CHỦ PC: Phiên bản Python, thư viện, IP LAN (192.168.1.6), cổng 5000, cổng COM USB.
2. KIỂM TRA KẾT NỐI TỚI RASPBERRY PI (192.168.1.7): Ping mạng LAN, cổng SSH (22), cổng Web Pi (8080), Camera RTSP (554).
3. IN BẢNG BÁO CÁO KẾT LUẬN TRƯỚC KHI CHẠY: Xác định rõ mục nào đã thông, mục nào đang chờ bật bên Pi.
4. MỞ SERVER & BÁO TRẠNG THÁI ĐỊNH KỲ: Nếu Pi chưa bật chương trình, cứ mỗi 5 giây sẽ báo trạng thái kết nối thực tế để người vận hành theo dõi cho đến khi bắt được tín hiệu từ Pi.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import importlib
import json
from pathlib import Path
import platform
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from check_connections import (
    auto_discover_pi_ip,
    check_local_port_free,
    check_ping,
    check_tcp_port,
    fetch_pi_live_state,
    get_pc_lan_ip,
    probe_esp32_serial,
    scan_pc_com_ports,
)
from mock_friend_server import (
    ACTIVE_DOWNLINK_XYZ,
    CONNECTED_VEHICLES,
    FriendServerHandler,
    SERVER_LOCK,
    ThreadingHTTPServer,
    VIOLATION_HISTORY,
    _start_server_gps_anchor,
    haversine_distance_km,
)

SIGNAL_LOG_PATH = SERVER_DIR / "signals_log.jsonl"
ACTIVE_PI_TARGET = {"ip": "192.168.1.7", "port": 8080}


def start_udp_discovery_responder(server_port: int = 5000) -> None:
    """Mở cổng UDP 5005 để trả lời tự động khi Raspberry Pi dò tìm Server PC trên mạng Wi-Fi / VLAN bất kỳ."""
    def _udp_loop():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", 5005))
        except Exception:
            return

        while True:
            try:
                data, addr = sock.recvfrom(1024)
                msg = json.loads(data.decode("utf-8", errors="replace"))
                if msg.get("magic") == "IOTV3_DISCOVER_SERVER":
                    pi_new_ip = msg.get("pi_ip") or addr[0]
                    if pi_new_ip and pi_new_ip not in ("127.0.0.1", "0.0.0.0"):
                        if ACTIVE_PI_TARGET["ip"] != pi_new_ip:
                            print(f"\n[🌐 TỰ ĐỘNG BẮT IP MỚI CỦA PI] Phát hiện Raspberry Pi trên Wi-Fi tại: {pi_new_ip}")
                            ACTIVE_PI_TARGET["ip"] = pi_new_ip
                    pc_now_ip = get_pc_lan_ip()
                    reply = json.dumps({
                        "magic": "IOTV3_SERVER_BEACON",
                        "server_url": f"http://{pc_now_ip}:{server_port}",
                        "pc_ip": pc_now_ip,
                        "port": server_port,
                    }).encode("utf-8")
                    sock.sendto(reply, addr)
            except Exception:
                time.sleep(0.2)

    threading.Thread(target=_udp_loop, name="udp-discovery-5005", daemon=True).start()


def run_preflight_diagnostics(pi_ip: str, pi_port: int, server_port: int, cam_ip: str = "192.168.1.210") -> dict:
    """Thực hiện kiểm tra toàn diện môi trường & kết nối trước khi khởi chạy Server."""
    pc_ip = get_pc_lan_ip()
    print("=" * 80)
    print("  🔎 BƯỚC 4A: KIỂM TRA MÔI TRƯỜNG & KẾT NỐI TRƯỚC KHI CHẠY SERVER (PRE-FLIGHT)")
    print("=" * 80)

    # 1. Kiểm tra môi trường phần mềm trên PC
    print("\n[1/4] 💻 MÔI TRƯỜNG MÁY CHỦ WINDOWS (PC HOST):")
    print(f"  • Hệ điều hành & Python : {platform.system()} {platform.release()} | Python {platform.python_version()}")
    missing_pkgs = []
    for pkg in ("serial", "sqlite3", "json", "urllib.request"):
        try:
            importlib.import_module(pkg)
        except Exception:
            missing_pkgs.append(pkg)
    if not missing_pkgs:
        print("  • Thư viện cốt lõi      : [OK] Đầy đủ (pyserial, sqlite3, json, urllib)")
    else:
        print(f"  • Thư viện cốt lõi      : [!] Thiếu gói: {', '.join(missing_pkgs)}")

    print(f"  • Địa chỉ IP LAN của PC : {pc_ip}")
    port_free = check_local_port_free(server_port)
    print(
        f"  • Cổng Server ({server_port})    : "
        f"{'[OK] Đang trống, sẵn sàng mở lắng nghe' if port_free else '[!] ĐANG BỊ CHIẾM (Có một tiến trình khác đang dùng cổng ' + str(server_port) + ')'}"
    )

    # 2. Kiểm tra cổng USB/COM trên PC (xác định vị trí cắm ESP32)
    print("\n[2/4] 🔌 KIỂM TRA VỊ TRÍ CẮM MẠCH ESP32 TRÊN PC:")
    com_ports = scan_pc_com_ports()
    esp_on_pc = False
    esp_pc_port = ""
    for dev, desc in com_ports:
        if any(k in desc.lower() for k in ("cp210", "ch340", "usb", "uart")):
            esp_on_pc = True
            esp_pc_port = dev
            esp_ok, esp_info = probe_esp32_serial(dev, 921600)
            print(f"  • Phát hiện ESP32 ở PC  : [!] Đang cắm tại {dev} ({desc})")
            print(f"    ↳ Tín hiệu trực tiếp  : {'[OK] ' + esp_info if esp_ok else '[!] ' + esp_info}")
            print("    ↳ 💡 Lưu ý: Nếu bạn muốn test trực tiếp trên Pi, hãy rút cáp USB ESP32 từ PC cắm sang Pi!")
    if not esp_on_pc:
        print("  • Cổng USB trên PC      : [OK] Không cắm ESP32 trên PC (Sẵn sàng nhận từ cổng USB của Raspberry Pi)")

    # 3. Kiểm tra đường truyền mạng LAN tới Raspberry Pi (Tự động dò IP nếu đổi Wi-Fi / VLAN)
    discovered_pi = auto_discover_pi_ip(pi_ip, pi_port)
    if discovered_pi and discovered_pi != pi_ip:
        print(f"\n  🔄 [TỰ ĐỘNG DÒ WI-FI MỚI] Phát hiện Raspberry Pi đổi sang IP mới: {discovered_pi} (thay vì {pi_ip})")
        pi_ip = discovered_pi
    ACTIVE_PI_TARGET["ip"] = pi_ip
    ACTIVE_PI_TARGET["port"] = pi_port

    print(f"\n[3/4] 🍓 KIỂM TRA KẾT NỐI MẠNG LAN TỚI RASPBERRY PI ({pi_ip}):")
    pi_ping_ok, pi_ping_msg = check_ping(pi_ip)
    ssh_ok = check_tcp_port(pi_ip, 22)
    pi_web_ok = check_tcp_port(pi_ip, pi_port)

    print(f"  • Ping mạng LAN tới Pi  : {'[OK] ĐÃ KẾT NỐI (' + pi_ping_msg + ')' if pi_ping_ok else '[X] MẤT KẾT NỐI (' + pi_ping_msg + ')'}")
    print(f"  • Cổng SSH Pi (Port 22) : {'[OK] ĐANG MỞ (Sẵn sàng nhận lệnh từ VS Code SSH)' if ssh_ok else '[X] KHÔNG MỞ'}")
    if pi_web_ok:
        print(f"  • Web Server Pi ({pi_port})  : [OK] ĐANG CHẠY — Bắt tín hiệu ngay lập tức!")
    else:
        print(f"  • Web Server Pi ({pi_port})  : [⏳ ĐANG CHỜ] Bên Pi chưa bật chương trình (Sẽ tự động bắt ngay khi bạn chạy Bước 6 trên Pi)")

    # 4. Kiểm tra Camera LAN (nếu có)
    print(f"\n[4/4] 📷 KIỂM TRA CAMERA IP LAN ({cam_ip}:554):")
    cam_ping_ok, _ = check_ping(cam_ip, timeout_ms=600)
    cam_rtsp_ok = check_tcp_port(cam_ip, 554, timeout_s=0.8) if cam_ping_ok else False
    if cam_rtsp_ok:
        print(f"  • Camera RTSP ({cam_ip}): [OK] ĐÃ KẾT NỐI TRỰC TIẾP TRÊN MẠNG LAN")
    else:
        print(f"  • Camera RTSP ({cam_ip}): [CẮM TRÊN CỔNG LAN RJ45 CỦA PI] Sẽ được Pi thu hình trực tiếp qua cổng eth0")

    # Tổng kết báo cáo trước khi mở Server
    print("\n" + "-" * 80)
    if pi_ping_ok and port_free:
        print("  ✅ BÁO CÁO KẾT LUẬN: MÔI TRƯỜNG PC & ĐƯỜNG TRUYỀN LAN TỚI PI ĐÃ HOÀN TOÀN ỔN ĐỊNH!")
    elif not port_free:
        print(f"  ❌ CẢNH BÁO: Cổng {server_port} đang bận. Hãy tắt cửa sổ server cũ trước khi chạy lại.")
    else:
        print(f"  ⚠️  CẢNH BÁO: Chưa Ping được tới Raspberry Pi ({pi_ip}). Kiểm tra lại Wi-Fi/IP của Pi.")
    print("-" * 80 + "\n")

    return {
        "pc_ip": pc_ip,
        "pi_ip": pi_ip,
        "port_free": port_free,
        "pi_ping_ok": pi_ping_ok,
        "ssh_ok": ssh_ok,
        "pi_web_ok": pi_web_ok,
        "esp_on_pc": esp_on_pc,
        "esp_pc_port": esp_pc_port,
    }


def append_signal_log(event_type: str, source: str, payload: dict) -> None:
    try:
        record = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event_type": event_type,
            "source": source,
            "payload": payload,
        }
        with SIGNAL_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def format_signal_summary(payload: dict, source_tag: str = "PUSH") -> str:
    vid = payload.get("vehicle_id") or payload.get("device_id") or "PI-CAM"
    ip = (
        payload.get("access_info", {}).get("local_ip")
        or payload.get("pi_ip")
        or "192.168.1.7"
    )
    esp = payload.get("esp", {})
    esp_conn = "OK" if (esp.get("connected") or esp.get("is_connected") or payload.get("esp_connected")) else "NGAT"
    mq = payload.get("mq135", {})
    co2 = mq.get("co2_ppm", payload.get("co2_ppm", "--"))
    gps = payload.get("gps", {})
    lat = float(gps.get("lat", payload.get("gps_lat", 0.0)))
    lon = float(gps.get("lon", payload.get("gps_lon", 0.0)))
    alt = gps.get("alt_m", gps.get("altitude_m", payload.get("gps_alt", 0.0)))
    spd = gps.get("speed_kmh", payload.get("gps_speed", 0.0))
    gps_src = gps.get("source", payload.get("gps_source", "--"))
    loa = payload.get("loa", payload.get("speaker", {}))
    voice = loa.get("current_voice") or loa.get("alert_level") or payload.get("alert", "NORMAL")
    hw = payload.get("hardware", payload.get("pi", {}))
    temp = hw.get("temp_c", hw.get("cpu_temp_c", payload.get("temp_c", "--")))
    ram = hw.get("ram_mb", hw.get("ram_used_mb", payload.get("ram_mb", "--")))

    return (
        f"[BẮT TÍN HIỆU {source_tag}] Xe={vid} ({ip}) | "
        f"ESP32={esp_conn} | CO2={co2} ppm | "
        f"XYZ=({lon:.6f}E, {lat:.6f}N, {alt}m | {spd}km/h | {gps_src}) | "
        f"Loa={voice} | SoC={temp}°C | RAM={ram}MB"
    )


class StandaloneServerHandler(FriendServerHandler):
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def do_POST(self):
        try:
            self._handle_post_internal()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def _handle_post_internal(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            payload = {}

        # Ghi nhận và hiển thị ngay lập tức khi nhận gói Telemetry từ Pi
        if self.path in ("/api/v1/telemetry", "/api/v1/pi/telemetry", "/esp32_data"):
            vid = payload.get("vehicle_id") or payload.get("device_id") or "XE-01-PI4"
            sender_ip = self.client_address[0] if self.client_address else ""
            if sender_ip and sender_ip not in ("127.0.0.1", "::1") and ACTIVE_PI_TARGET["ip"] != sender_ip:
                ACTIVE_PI_TARGET["ip"] = sender_ip
            with SERVER_LOCK:
                CONNECTED_VEHICLES[vid] = payload
                downlink_xyz = dict(ACTIVE_DOWNLINK_XYZ)

            append_signal_log("TELEMETRY_POST", self.client_address[0], payload)
            print(format_signal_summary(payload, source_tag="POST"))
            self._send_json({"ok": True, "ack": "TELEMETRY_RECEIVED", "vehicle_id": vid, "downlink_xyz": downlink_xyz})
            return

        elif self.path in ("/api/v1/violation", "/api/v1/violations"):
            vid = payload.get("vehicle_id") or payload.get("device_id") or "XE-01-PI4"
            rule = payload.get("alert_type", "VIOLATION")
            dur = payload.get("duration_seconds", 0.0)
            has_img = "CO (Base64)" if payload.get("evidence_image") else "KHONG"
            with SERVER_LOCK:
                VIOLATION_HISTORY.append(payload)
                if len(VIOLATION_HISTORY) > 200:
                    VIOLATION_HISTORY.pop(0)

            append_signal_log("VIOLATION_POST", self.client_address[0], payload)
            print(f"\n[🚨 BẮT SỰ KIỆN VI PHẠM TỪ PI] Xe={vid} | Cảnh báo={rule} | Thời lượng={dur}s | Ảnh bằng chứng={has_img}")
            self._send_json({"ok": True, "ack": "VIOLATION_LOGGED", "event_id": payload.get("event_id")})
            return

        elif self.path == "/api/v1/chunks":
            append_signal_log("IMAGE_CHUNK", self.client_address[0], payload)
            print(
                f"[BẮT MẢNH ẢNH G11] Từ {self.client_address[0]} | "
                f"Sự cố={payload.get('incident_id', '--')} | "
                f"Chunk={payload.get('chunk_index', 1)}/{payload.get('total_chunks', 1)}"
            )
            self._send_json({"ok": True, "ack": "CHUNK_RECEIVED"})
            return

        # Các path khác fallback về handler cha
        super().do_POST()


def start_active_pi_poller(pi_ip: str, pi_port: int = 8080, interval_s: float = 2.0) -> None:
    endpoints = ("/api/status", "/api/state")
    ACTIVE_PI_TARGET["ip"] = pi_ip
    ACTIVE_PI_TARGET["port"] = pi_port

    def _poll_loop():
        last_xyz_push = 0.0
        last_wait_log = 0.0
        last_rediscover = 0.0
        was_online = False
        active_endpoint = endpoints[0]

        while True:
            now = time.time()
            cur_pi_ip = ACTIVE_PI_TARGET["ip"]
            cur_pi_port = ACTIVE_PI_TARGET["port"]
            loc_url = f"http://{cur_pi_ip}:{cur_pi_port}/api/set_pc_location"
            data_fetched = False

            for ep in endpoints:
                state_url = f"http://{cur_pi_ip}:{cur_pi_port}{ep}"
                try:
                    req = urllib.request.Request(state_url, method="GET")
                    with urllib.request.urlopen(req, timeout=1.2) as resp:
                        if resp.status == 200:
                            raw = resp.read().decode("utf-8", errors="replace")
                            state = json.loads(raw)
                            active_endpoint = ep
                            data_fetched = True

                            if not was_online:
                                print(f"\n[🎉 KẾT NỐI PI THÀNH CÔNG] Đã bắt được luồng tín hiệu từ {state_url}!")
                                was_online = True

                            # Trích xuất dữ liệu từ /api/status hoặc /api/state
                            esp_data = state.get("esp_info") or state.get("esp") or {}
                            gps_data = state.get("gps_snap") or state.get("gps") or {}
                            hw_data = state.get("hw") or state.get("hardware") or {}
                            st_data = state.get("state") or state.get("driver_state") or state

                            vid = "XE-01-PI4"
                            lat = float(gps_data.get("lat") or state.get("gps_lat") or 16.0685)
                            lon = float(gps_data.get("lon") or state.get("gps_lon") or 108.2215)
                            alt = float(gps_data.get("altitude_m") or state.get("gps_alt") or 8.5)
                            spd = float(gps_data.get("speed_kmh") or esp_data.get("speed_kmh") or state.get("gps_speed") or 0.0)

                            anchor_lat = float(ACTIVE_DOWNLINK_XYZ.get("lat", 16.0685))
                            anchor_lon = float(ACTIVE_DOWNLINK_XYZ.get("lon", 108.2215))
                            dist_km = (
                                haversine_distance_km(lat, lon, anchor_lat, anchor_lon)
                                if haversine_distance_km
                                else 0.0
                            )

                            co2_val = esp_data.get("co2_ppm") or state.get("co2_ppm", 400.0)
                            co2_lvl = esp_data.get("co2_level") or state.get("co2_level", 0)
                            esp_is_conn = bool(esp_data.get("connected") or state.get("esp_connected", False))
                            cam_fps = float(st_data.get("ai_fps") or state.get("fps") or 0.0)

                            normalized = {
                                "vehicle_id": vid,
                                "vehicle_name": f"Raspberry Pi 4 ({cur_pi_ip})",
                                "driver_name": "Tài xế 01",
                                "timestamp": datetime.now().isoformat(),
                                "distance_to_anchor_km": round(dist_km, 3),
                                "access_info": {
                                    "local_ip": cur_pi_ip,
                                    "web_port": cur_pi_port,
                                    "dashboard_url": f"http://{cur_pi_ip}:{cur_pi_port}",
                                    "stream_url": f"http://{cur_pi_ip}:{cur_pi_port}/stream",
                                },
                                "camera": {
                                    "status": "ONLINE" if cam_fps > 0 else "CHO_CAMERA",
                                    "fps": cam_fps,
                                    "latency_ms": float(st_data.get("pipeline_latency_ms") or state.get("yolo_ms") or 0.0),
                                    "resolution": "640x480",
                                },
                                "esp": {
                                    "connected": esp_is_conn,
                                    "port": esp_data.get("port") or state.get("esp_port", "/dev/ttyUSB0"),
                                    "reset_count": esp_data.get("reset_count", 0),
                                    "latest_telemetry": esp_data.get("latest_telemetry") or state.get("esp_raw", ""),
                                },
                                "gps": {
                                    "lat": lat,
                                    "lon": lon,
                                    "x": lon,
                                    "y": lat,
                                    "z": alt,
                                    "altitude_m": alt,
                                    "alt_m": alt,
                                    "speed_kmh": spd,
                                    "satellites": gps_data.get("satellites") or state.get("gps_sats", 0),
                                    "status": gps_data.get("status") or state.get("gps_status", "SERVER_XYZ"),
                                    "source": gps_data.get("source") or state.get("gps_source", "server_xyz"),
                                    "is_valid": True,
                                    "google_maps_url": f"https://www.google.com/maps?q={lat},{lon}",
                                },
                                "mq135": {
                                    "co2_ppm": co2_val,
                                    "gas_level": co2_lvl,
                                    "status_label": "Nguy hiểm" if co2_lvl >= 2 else ("Cảnh báo bí khí" if co2_lvl == 1 else "An toàn"),
                                },
                                "speaker": {
                                    "current_voice": st_data.get("confirmed_alert") or state.get("alert", "NORMAL"),
                                    "is_playing": (st_data.get("confirmed_alert") or state.get("alert", "NORMAL")) != "NORMAL",
                                    "volume": 100,
                                },
                                "driver_state": {
                                    "alert": st_data.get("confirmed_alert") or state.get("alert", "NORMAL"),
                                    "eye_duration": float(st_data.get("eye_duration") or state.get("eye_duration") or 0.0),
                                    "mouth_duration": float(st_data.get("mouth_duration") or state.get("mouth_duration") or 0.0),
                                    "distracted_duration": float(st_data.get("distracted_duration") or state.get("distracted_duration") or 0.0),
                                },
                                "hardware": {
                                    "cpu_percent": float(hw_data.get("cpu_percent") or state.get("cpu_percent") or 0.0),
                                    "ram_mb": float(hw_data.get("ram_mb") or state.get("ram_mb") or 0.0),
                                    "temp_c": hw_data.get("temp_c") or state.get("temp_c"),
                                    "throttled": hw_data.get("throttled") or state.get("throttled_hex", "0x0"),
                                },
                            }

                            with SERVER_LOCK:
                                CONNECTED_VEHICLES[vid] = normalized

                            append_signal_log("POLL_STATE", cur_pi_ip, normalized)
                            print(format_signal_summary(normalized, source_tag="PI-LIVE"))
                            break
                except Exception:
                    continue

            if not data_fetched:
                if was_online:
                    print(f"[⚠️ MẤT TÍN HIỆU PI] Tạm mất kết nối tới http://{cur_pi_ip}:{cur_pi_port}, đang tiếp tục dò lại...")
                    was_online = False
                elif now - last_wait_log >= 6.0:
                    last_wait_log = now
                    if now - last_rediscover >= 12.0:
                        last_rediscover = now
                        new_pi = auto_discover_pi_ip(cur_pi_ip, cur_pi_port)
                        if new_pi and new_pi != cur_pi_ip:
                            print(f"[🔄 ĐỔI MẠNG WI-FI] Đã tự động dò thấy IP mới của Raspberry Pi: {new_pi}")
                            ACTIVE_PI_TARGET["ip"] = new_pi
                            cur_pi_ip = new_pi
                    ssh_alive = check_tcp_port(cur_pi_ip, 22, timeout_s=0.8)
                    ts_str = datetime.now().strftime("%H:%M:%S")
                    if ssh_alive:
                        print(
                            f"[{ts_str}] [⏳ ĐANG CHỜ PI BẬT CODE] Mạng LAN tới Pi ({cur_pi_ip}): OK | "
                            f"SSH(22): MỞ | Web Pi({cur_pi_port}): CHƯA BẬT -> Hãy chạy trên SSH của Pi: bash AI/deploy/test_pi_web.sh"
                        )
                    else:
                        print(
                            f"[{ts_str}] [❌ CHƯA THẤY PI TRÊN MẠNG LAN] Đang tự động quét dải Wi-Fi tìm Pi ({cur_pi_ip})..."
                        )

            # Đồng bộ tọa độ mốc PC + URL Server PC xuống Pi
            if data_fetched and now - last_xyz_push >= 6.0:
                last_xyz_push = now
                try:
                    pc_now_ip = get_pc_lan_ip()
                    with SERVER_LOCK:
                        xyz_data = {
                            "lat": ACTIVE_DOWNLINK_XYZ.get("lat", 16.0685),
                            "lon": ACTIVE_DOWNLINK_XYZ.get("lon", 108.2215),
                            "alt": ACTIVE_DOWNLINK_XYZ.get("alt_m", 8.5),
                            "speed_kmh": ACTIVE_DOWNLINK_XYZ.get("speed_kmh", 0.0),
                            "source": "pc_server_anchor",
                            "server_url": f"http://{pc_now_ip}:5000",
                        }
                    post_req = urllib.request.Request(
                        loc_url,
                        data=json.dumps(xyz_data).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(post_req, timeout=1.2):
                        pass
                except Exception:
                    pass

            time.sleep(interval_s)

    t = threading.Thread(target=_poll_loop, name="active-pi-poller", daemon=True)
    t.start()


def main():
    parser = argparse.ArgumentParser(description="Server Trung Tâm Độc Lập (E:\\AI\\Server) Bắt Tín Hiệu Từ Raspberry Pi")
    parser.add_argument("--port", type=int, default=5000, help="Cổng lắng nghe của Server trên PC (mặc định: 5000)")
    parser.add_argument("--pi-ip", type=str, default="192.168.1.7", help="Địa chỉ IP của Raspberry Pi (tự động dò khi đổi Wi-Fi)")
    parser.add_argument("--pi-port", type=int, default=8080, help="Cổng Web của Raspberry Pi (mặc định: 8080)")
    parser.add_argument("--skip-check", action="store_true", help="Bỏ qua bước kiểm tra môi trường đầu vào")
    args = parser.parse_args()

    # Tự động chạy Kiểm tra môi trường & kết nối ngay khi khởi động Bước 4
    if not args.skip_check:
        diag = run_preflight_diagnostics(pi_ip=args.pi_ip, pi_port=args.pi_port, server_port=args.port)
        pc_ip = diag["pc_ip"]
        args.pi_ip = diag.get("pi_ip", args.pi_ip)
        if not diag["port_free"]:
            sys.exit(1)
    else:
        pc_ip = get_pc_lan_ip()
        args.pi_ip = auto_discover_pi_ip(args.pi_ip, args.pi_port)

    _start_server_gps_anchor()
    start_udp_discovery_responder(server_port=args.port)
    start_active_pi_poller(pi_ip=args.pi_ip, pi_port=args.pi_port, interval_s=2.0)

    server = ThreadingHTTPServer(("0.0.0.0", args.port), StandaloneServerHandler)
    print("=" * 80)
    print("  📡 BƯỚC 4B: SERVER TRUNG TÂM (E:\\AI\\Server) ĐÃ MỞ CỔNG SẴN SÀNG BẮT TÍN HIỆU")
    print("=" * 80)
    print(f"  🖥️  IP Máy Chủ PC (LAN)       : http://{pc_ip}:{args.port}")
    print(f"  🌐 Giao diện Web Giám Sát     : http://localhost:{args.port}")
    print(f"  🎯 Mục tiêu Raspberry Pi      : http://{args.pi_ip}:{args.pi_port}")
    print(f"  📥 Endpoint Nhận Telemetry    : http://{pc_ip}:{args.port}/api/v1/telemetry")
    print(f"  🚨 Endpoint Nhận Vi Phạm      : http://{pc_ip}:{args.port}/api/v1/violation")
    print(f"  💾 File Lưu Nhật Ký Tín Hiệu  : {SIGNAL_LOG_PATH}")
    print("=" * 80)
    print("  Đang lắng nghe và tự động báo cáo trạng thái kết nối tới Pi mỗi 6 giây... (Ctrl+C để dừng)\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[DỪNG] Đã đóng Server bắt tín hiệu.")
        server.shutdown()


if __name__ == "__main__":
    main()
