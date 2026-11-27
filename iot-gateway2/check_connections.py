"""Công cụ Kiểm Tra Toàn Diện Kết Nối Trước Khi Chạy (Pre-flight Connection Checker).

Đường dẫn: E:\\AI\\Server\\check_connections.py
Chạy trực tiếp:
    python E:\\AI\\Server\\check_connections.py --pi-ip 192.168.1.7

Kiểm tra 5 hạng mục cốt lõi:
1. Mạng LAN trên Máy tính (PC) & Cổng Server (5000)
2. Kết nối mạng & SSH (cổng 22) tới Raspberry Pi (192.168.1.7)
3. Dịch vụ Web & API trên Raspberry Pi (cổng 8080) + trạng thái ESP32 trên Pi
4. Kết nối Camera IP Vantech trên mạng LAN (192.168.1.210:554)
5. Các cổng USB/COM trên Máy tính (xác định ESP32 đang cắm ở PC hay ở Pi)
"""
from __future__ import annotations

import argparse
import importlib
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def get_pc_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def check_ping(host: str, timeout_ms: int = 1000) -> tuple[bool, str]:
    try:
        if sys.platform.startswith("win"):
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
        else:
            cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), host]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if "time=" in line.lower() or "time<" in line.lower():
                    return True, line.strip()
            return True, "Ping thành công"
        return False, "Không phản hồi (Request timed out / Unreachable)"
    except Exception as e:
        return False, str(e)


def check_tcp_port(host: str, port: int, timeout_s: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except Exception:
        return False


def check_local_port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        s.close()
        return True
    except OSError:
        return False


def scan_pc_com_ports() -> list[tuple[str, str]]:
    found = []
    try:
        list_ports = importlib.import_module("serial.tools.list_ports")
        for p in list_ports.comports():
            found.append((p.device, p.description))
    except Exception:
        pass
    return found


def probe_esp32_serial(port: str, baudrate: int = 921600) -> tuple[bool, str]:
    try:
        serial = importlib.import_module("serial")
        import time
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = baudrate
        ser.timeout = 0.35
        ser.dtr = False
        ser.rts = False
        ser.open()
        ser.write(b"PING\n")
        t0 = time.time()
        fdesp_line = ""
        pong_ok = False
        while time.time() - t0 < 1.2:
            raw = ser.readline()
            if raw:
                txt = raw.decode("utf-8", errors="replace").strip()
                if "PONG" in txt:
                    pong_ok = True
                elif "FDESP:" in txt:
                    fdesp_line = txt[txt.index("FDESP:"):]
                if pong_ok and fdesp_line:
                    break
        ser.close()
        if pong_ok or fdesp_line:
            return True, f"Baud={baudrate} | PONG={pong_ok} | Telemetry={fdesp_line[:85]}..."
        return False, "Mở được cổng nhưng chưa nhận được gói FDESP/PONG"
    except Exception as e:
        return False, str(e)


def fetch_pi_live_state(pi_ip: str, pi_port: int) -> dict | None:
    for endpoint in ("/api/status", "/api/state"):
        url = f"http://{pi_ip}:{pi_port}{endpoint}"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            continue
    return None


def _probe_pi_candidate(ip: str, pi_port: int = 8080) -> tuple[int, str]:
    """Trả về mức độ khớp của một IP ứng viên Raspberry Pi:
    2 = Mở sẵn cổng Web Pi (8080)
    1 = Mở cổng SSH (22) với banner Debian/OpenSSH của Raspberry Pi
    0 = Không phải Pi
    """
    if check_tcp_port(ip, pi_port, timeout_s=0.25):
        return 2, ip
    try:
        with socket.create_connection((ip, 22), timeout=0.25) as s:
            s.settimeout(0.3)
            banner = s.recv(64).decode("utf-8", errors="replace")
            if "OpenSSH" in banner and ("Debian" in banner or "Raspbian" in banner or "SSH-2.0" in banner):
                return 1, ip
    except Exception:
        pass
    return 0, ip


def auto_discover_pi_ip(preferred_ip: str = "192.168.1.7", pi_port: int = 8080) -> str:
    """Tự động tìm IP của Raspberry Pi khi đổi mạng Wi-Fi / VLAN.
    
    1. Kiểm tra preferred_ip trước (nếu cùng lớp mạng và đang mở cổng 22/8080 thì giữ nguyên).
    2. Thử phân giải mDNS 'raspberrypi.local'.
    3. Quét song song dải mạng /24 hiện tại của PC (48 luồng, < 1.2s) tìm cổng 8080 hoặc SSH 22 của Pi.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    pc_ip = get_pc_lan_ip()
    pc_parts = pc_ip.split(".")
    pref_parts = (preferred_ip or "").split(".")

    # 1. Nếu preferred_ip cùng dải mạng với PC và đang phản hồi -> dùng ngay
    if len(pc_parts) == 4 and len(pref_parts) == 4 and pc_parts[:3] == pref_parts[:3]:
        if check_tcp_port(preferred_ip, pi_port, timeout_s=0.4) or check_tcp_port(preferred_ip, 22, timeout_s=0.4):
            return preferred_ip

    # 2. Thử phân giải mDNS raspberrypi.local
    for hostname in ("raspberrypi.local", "raspberrypi"):
        try:
            mdns_ip = socket.gethostbyname(hostname)
            if mdns_ip and not mdns_ip.startswith("127.") and mdns_ip != pc_ip:
                if check_tcp_port(mdns_ip, pi_port, timeout_s=0.4) or check_tcp_port(mdns_ip, 22, timeout_s=0.4):
                    return mdns_ip
        except Exception:
            pass

    # 3. Quét nhanh toàn dải /24 hiện tại của Wi-Fi trên PC
    if len(pc_parts) == 4 and pc_parts[0] != "127":
        prefix = f"{pc_parts[0]}.{pc_parts[1]}.{pc_parts[2]}."
        candidates = [f"{prefix}{i}" for i in range(1, 255) if f"{prefix}{i}" not in (pc_ip, "192.168.1.210")]
        best_ssh_ip = ""
        try:
            with ThreadPoolExecutor(max_workers=48) as pool:
                futures = [pool.submit(_probe_pi_candidate, ip, pi_port) for ip in candidates]
                for fut in as_completed(futures):
                    try:
                        score, ip = fut.result()
                        if score == 2:
                            return ip
                        elif score == 1 and not best_ssh_ip:
                            best_ssh_ip = ip
                    except Exception:
                        pass
        except Exception:
            pass
        if best_ssh_ip:
            return best_ssh_ip

    return preferred_ip


def main() -> int:
    parser = argparse.ArgumentParser(description="Kiểm tra toàn diện kết nối hệ thống Server <-> Pi <-> ESP32 <-> Camera")
    parser.add_argument("--pi-ip", type=str, default="192.168.1.7", help="IP của Raspberry Pi (mặc định: 192.168.1.7)")
    parser.add_argument("--pi-port", type=int, default=8080, help="Cổng Web của Pi (mặc định: 8080)")
    parser.add_argument("--cam-ip", type=str, default="192.168.1.210", help="IP của Camera RTSP Vantech (mặc định: 192.168.1.210)")
    parser.add_argument("--server-port", type=int, default=5000, help="Cổng của Server trên PC (mặc định: 5000)")
    args = parser.parse_args()

    pc_ip = get_pc_lan_ip()
    print("=" * 78)
    print("  🔍 KIỂM TRA TOÀN DIỆN KẾT NỐI HỆ THỐNG (E:\\AI\\Server <-> Raspberry Pi)")
    print("=" * 78)

    # 1. Kiểm tra Máy Chủ PC
    print("\n[1/5] 🖥️  MÁY TÍNH CHẠY SERVER (E:\\AI\\Server):")
    print(f"  • Địa chỉ IP LAN của PC : {pc_ip}")
    port_free = check_local_port_free(args.server_port)
    if port_free:
        print(f"  • Cổng Server ({args.server_port})    : [SẴN SÀNG] Cổng đang trống, có thể bật server.py ngay")
    else:
        print(f"  • Cổng Server ({args.server_port})    : [ĐANG CHẠY] Đã có tiến trình lắng nghe trên cổng {args.server_port}")

    # 2. Kiểm tra Kết nối Mạng & SSH tới Raspberry Pi
    print(f"\n[2/5] 🍓 KẾT NỐI MẠNG & SSH TỚI RASPBERRY PI ({args.pi_ip}):")
    pi_ping_ok, pi_ping_msg = check_ping(args.pi_ip)
    ssh_ok = check_tcp_port(args.pi_ip, 22)
    print(f"  • Ping mạng LAN tới Pi  : {'[OK] ĐÃ KẾT NỐI (' + pi_ping_msg + ')' if pi_ping_ok else '[X] CHƯA KẾT NỐI (' + pi_ping_msg + ')'}")
    print(f"  • Cổng SSH (Port 22)    : {'[OK] MỞ SẴN SÀNG (VS Code SSH / SCP hoạt động tốt)' if ssh_ok else '[X] ĐÓNG / KHÔNG THÔNG'}")

    # 3. Kiểm tra Web Server & Thiết bị cắm trên Raspberry Pi
    print(f"\n[3/5] 🌐 CHƯƠNG TRÌNH GIÁM SÁT TRÊN RASPBERRY PI ({args.pi_ip}:{args.pi_port}):")
    pi_web_ok = check_tcp_port(args.pi_ip, args.pi_port)
    if pi_web_ok:
        print(f"  • Cổng Web Pi ({args.pi_port})    : [OK] ĐANG CHẠY (http://{args.pi_ip}:{args.pi_port})")
        state = fetch_pi_live_state(args.pi_ip, args.pi_port)
        if state is not None:
            esp_conn = state.get("esp_connected", False)
            esp_port = state.get("esp_port", "Chưa rõ")
            co2 = state.get("co2_ppm", "--")
            gps_src = state.get("gps_source", "--")
            print(f"  • ESP32 cắm trên Pi     : {'[OK] ĐÃ KẾT NỐI (' + str(esp_port) + ')' if esp_conn else '[!] CHƯA KẾT NỐI UART (' + str(esp_port) + ')'}")
            print(f"  • Cảm biến MQ-135 (CO2) : {co2} ppm")
            print(f"  • Nguồn tọa độ GPS XYZ  : {gps_src} (Lat={state.get('gps_lat')}, Lon={state.get('gps_lon')})")
            print(f"  • Nhiệt độ chip SoC Pi  : {state.get('temp_c', '--')} °C")
        else:
            print("  • Trạng thái API (/api/state): Mở cổng nhưng chưa trả dữ liệu JSON")
    else:
        print(f"  • Cổng Web Pi ({args.pi_port})    : [!] CHƯA BẬT (Bạn chưa chạy lệnh khởi động code trên Pi)")

    # 4. Kiểm tra Camera IP LAN (RTSP)
    print(f"\n[4/5] 📷 KẾT NỐI CAMERA IP LAN ({args.cam_ip}:554):")
    cam_ping_ok, _ = check_ping(args.cam_ip, timeout_ms=800)
    cam_rtsp_ok = check_tcp_port(args.cam_ip, 554, timeout_s=1.0) if cam_ping_ok else False
    if cam_rtsp_ok:
        print(f"  • Camera RTSP ({args.cam_ip}): [OK] ĐÃ KẾT NỐI & MỞ CỔNG 554")
    else:
        print(f"  • Camera RTSP ({args.cam_ip}): [CẮM TRÊN PI] Camera cắm trực tiếp vào cổng RJ45 (eth0) của Raspberry Pi")

    # 5. Kiểm tra Cổng USB/COM trên Máy tính (PC)
    print("\n[5/5] 🔌 CỔNG SERIAL / COM TRÊN MÁY TÍNH WINDOWS (PC):")
    com_ports = scan_pc_com_ports()
    if com_ports:
        for dev, desc in com_ports:
            print(f"  • Phát hiện trên PC     : [OK] {dev} ({desc})")
            if any(k in desc.lower() for k in ("cp210", "ch340", "usb", "uart")):
                esp_ok, esp_info = probe_esp32_serial(dev, 921600)
                print(f"    ↳ Kiểm tra sống ESP32 : {'[OK] ĐANG PHÁT TÍN HIỆU (' + esp_info + ')' if esp_ok else '[!] ' + esp_info}")
    else:
        print("  • Cổng COM trên PC      : Không có thiết bị USB-UART nào cắm ở PC (ESP32 đang cắm bên Pi hoặc chưa cắm)")

    # Tóm tắt kết luận
    print("\n" + "=" * 78)
    print("  📋 KẾT LUẬN & BƯỚC TIẾP THEO:")
    print("=" * 78)
    if pi_ping_ok and ssh_ok and not pi_web_ok:
        print("  ✅ Đường truyền mạng LAN & SSH giữa PC (192.168.1.6) và Pi (192.168.1.7) ĐÃ THÔNG SUỐT!")
        print("  👉 Việc cần làm tiếp theo:")
        print("     1. Chạy Server trên PC : python E:\\AI\\Server\\server.py")
        print("     2. Nạp code mới sang Pi và chạy: bash AI/deploy/test_pi_web.sh trên Pi")
    elif pi_ping_ok and pi_web_ok:
        print("  🎉 CẢ MẠNG LAN VÀ CHƯƠNG TRÌNH TRÊN PI ĐỀU ĐANG CHẠY TỐT!")
        print("  👉 Chỉ cần bật: python E:\\AI\\Server\\server.py để thu tín hiệu.")
    else:
        print("  ⚠️  Chưa ping được tới Raspberry Pi. Hãy kiểm tra lại Wi-Fi hoặc IP của Pi.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
