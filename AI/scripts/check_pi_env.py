"""Script kiểm tra toàn diện phần cứng & kết nối trực tiếp trên SSH của Raspberry Pi 4 (IOTv3).

Chạy trực tiếp trên Terminal SSH của Raspberry Pi:
    python3 AI/scripts/check_pi_env.py
hoặc:
    bash AI/deploy/check_pi.sh
"""

from __future__ import annotations

import glob
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

repo_root = Path(__file__).resolve().parents[2]
for p in (str(repo_root), str(repo_root / "AI"), str(repo_root / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

# Tự động nạp file cấu hình AI/deploy/pi.env nếu tồn tại
env_file = repo_root / "AI" / "deploy" / "pi.env"
if env_file.exists():
    for raw_line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _get_pi_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _check_tcp(host: str, port: int, timeout_s: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except Exception:
        return False


def _probe_esp32_uart(port: str) -> tuple[bool, int, str, dict]:
    """Dò ESP32 trên cổng Serial ở tốc độ 921600 và 115200 baud."""
    serial = importlib.import_module("serial")
    for baud in (921600, 115200):
        try:
            ser = serial.Serial()
            ser.port = port
            ser.baudrate = baud
            ser.timeout = 0.35
            ser.dtr = False
            ser.rts = False
            ser.open()
            ser.write(b"PING\n")
            t0 = time.time()
            pong = False
            fdesp_str = ""
            parsed_json = {}
            while time.time() - t0 < 2.2:
                raw = ser.readline()
                if not raw:
                    continue
                txt = raw.decode("utf-8", errors="replace").strip()
                if "PONG" in txt:
                    pong = True
                if "FDESP:" in txt:
                    fdesp_str = txt[txt.index("FDESP:") :]
                    try:
                        parsed_json = json.loads(fdesp_str[6:])
                    except Exception:
                        pass
                elif txt.startswith("CO2:") and not fdesp_str:
                    fdesp_str = txt
                if pong and fdesp_str:
                    break
            ser.close()
            if pong or fdesp_str:
                return True, baud, fdesp_str, parsed_json
        except Exception:
            pass
    return False, 0, "", {}


print("=" * 74)
print("  🔍 KIỂM TRA TOÀN DIỆN KẾT NỐI TRÊN SSH RASPBERRY PI 4 (IOTv3)")
print("=" * 74)

# ------------------------------------------------------------------------------
# [1/6] Kiểm tra Nhiệt độ & Nguồn điện SoC Raspberry Pi
# ------------------------------------------------------------------------------
print("\n[1/6] 🌡️  PHẦN CỨNG CHIP SoC RASPBERRY PI & NGUỒN ĐIỆN:")
temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
if temp_path.exists():
    try:
        temp_c = float(temp_path.read_text().strip()) / 1000.0
        status_temp = "[OK] Mát & Ổn định" if temp_c < 75.0 else "[!] CẢNH BÁO NÓNG (>75°C)"
        print(f"  • Nhiệt độ chip SoC    : {temp_c:.1f} °C — {status_temp}")
    except Exception as e:
        print(f"  • Lỗi đọc nhiệt độ     : {e}")
else:
    print("  • Nhiệt độ chip SoC    : Không truy vấn được (Môi trường không phải Linux Pi)")

try:
    throttled = subprocess.check_output(["vcgencmd", "get_throttled"], text=True, stderr=subprocess.DEVNULL).strip()
    code_hex = int(throttled.split("=")[-1], 16) if "=" in throttled else 0
    if code_hex == 0:
        print(f"  • Nguồn điện 5V Pi     : [OK] Ổn định 100% ({throttled})")
    else:
        print(f"  • Nguồn điện 5V Pi     : [!] Phát hiện cờ sụt áp/quá nhiệt ({throttled})")
        if code_hex & 0x1:
            print("    ↳ [CẢNH BÁO] Đang bị sụt áp nguồn (< 4.63V)! Kiểm tra nguồn cấp hoặc tải loa.")
except Exception:
    print("  • Nguồn điện 5V Pi     : Bỏ qua vcgencmd")

# ------------------------------------------------------------------------------
# [2/6] Kiểm tra Mạng LAN của Pi & Tự động dò Kết nối về Server trên PC
# ------------------------------------------------------------------------------
print("\n[2/6] 🌐 MẠNG LAN CỦA PI & TỰ ĐỘNG DÒ SERVER PC (HỖ TRỢ MỌI WI-FI / VLAN):")
pi_ip = _get_pi_ip()
server_url = os.getenv("REMOTE_SERVER_URL", "http://192.168.1.6:5000")
try:
    from AI.scripts.cloud_sync import auto_discover_pc_server
    discovered_url = auto_discover_pc_server(server_url)
    if discovered_url and discovered_url != server_url:
        print(f"  • Tự động dò Wi-Fi mới : [OK] Phát hiện Server PC đổi sang IP mới: {discovered_url}")
        server_url = discovered_url
        os.environ["REMOTE_SERVER_URL"] = server_url
except Exception:
    pass

parsed_srv = urlparse(server_url)
srv_host = parsed_srv.hostname or "192.168.1.6"
srv_port = parsed_srv.port or 5000

print(f"  • Địa chỉ IP của Pi    : {pi_ip} (Web Dashboard Pi sẽ mở tại http://{pi_ip}:8080)")
print(f"  • Địa chỉ Server PC    : {server_url}")

ping_ok = subprocess.run(
    ["ping", "-c" if not sys.platform.startswith("win") else "-n", "1", "-W" if not sys.platform.startswith("win") else "-w", "1", srv_host],
    capture_output=True,
).returncode == 0
srv_port_ok = _check_tcp(srv_host, srv_port, timeout_s=1.0)

print(f"  • Ping từ Pi -> PC     : {'[OK] THÔNG MẠNG LAN (' + srv_host + ')' if (ping_ok or srv_port_ok) else '[⏳ ĐANG CHỜ] Chưa thấy PC (' + srv_host + ') — Sẽ tự động bắt ngay khi bật server.py trên PC'}")
print(
    f"  • Cổng Server PC ({srv_port}): "
    f"{'[OK] ĐÃ KẾT NỐI (Server trên PC đang bật sẵn sàng nhận tín hiệu)' if srv_port_ok else '[⏳ ĐANG CHỜ] Chưa bật server.py trên PC (CloudSync sẽ tự dò ngầm liên tục)'}"
)

# ------------------------------------------------------------------------------
# [3/6] Kiểm tra Phân quyền User trên Raspberry Pi (dialout, video)
# ------------------------------------------------------------------------------
print("\n[3/6] 🔐 PHÂN QUYỀN TRUY CẬP PHẦN CỨNG CỦA USER TRÊN PI:")
user = os.getenv("USER", "raspberrypi")
try:
    groups = subprocess.check_output(["groups", user], text=True, stderr=subprocess.DEVNULL)
    has_dialout = "dialout" in groups
    has_video = "video" in groups
    print(f"  • Quyền UART (dialout) : {'[OK] Đã cấp quyền' if has_dialout else '[X] THIẾU (Chạy: sudo usermod -aG dialout $USER)'}")
    print(f"  • Quyền Camera (video) : {'[OK] Đã cấp quyền' if has_video else '[X] THIẾU (Chạy: sudo usermod -aG video $USER)'}")
except Exception:
    print("  • Bỏ qua kiểm tra groups (Môi trường Windows)")

# ------------------------------------------------------------------------------
# [4/6] Kiểm tra Kết nối ESP32 + MQ-135 + GPS qua cổng USB/UART trên Pi
# ------------------------------------------------------------------------------
print("\n[4/6] 🔌 KẾT NỐI MẠCH ESP32 + MQ-135 + GPS TRÊN CỔNG USB CỦA PI:")
usb_ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
if not usb_ports and sys.platform.startswith("win"):
    from AI.scripts.esp_communication import auto_detect_serial_port
    detected = auto_detect_serial_port(os.getenv("ESP_PORT"))
    if detected:
        usb_ports = [detected]

if usb_ports:
    print(f"  • Cổng USB phát hiện   : {', '.join(usb_ports)}")
    target_port = usb_ports[0]
    ok_esp, baud_esp, raw_line, jdata = _probe_esp32_uart(target_port)
    if ok_esp:
        print(f"  • Giao tiếp UART ESP32 : [OK] ĐÃ KẾT NỐI ({target_port} @ {baud_esp} baud)")
        print(f"  • Bản tin thu trực tiếp: {raw_line[:95]}")
        if jdata:
            print(
                f"    ↳ Cảm biến MQ-135    : CO2 = {jdata.get('co2_ppm')} ppm (Cấp cảnh báo: {jdata.get('co2_level')})"
            )
            print(
                f"    ↳ Cảm biến GPS NEO   : Fix = {jdata.get('gps_ready')} | Vệ tinh = {jdata.get('sats')} | "
                f"Lat = {jdata.get('gps_lat')} | Lon = {jdata.get('gps_lon')}"
            )
    else:
        print(f"  • Giao tiếp UART ESP32 : [!] Có cổng {target_port} nhưng chưa nhận được gói FDESP/PONG")
else:
    print("  • Cổng USB phát hiện   : [X] CHƯA PHÁT HIỆN (/dev/ttyUSB0 không tồn tại trên Pi)")
    print("    ↳ 💡 HƯỚNG DẪN: Mạch ESP32 đang cắm bên máy tính Windows (COM3) hoặc chưa cắm cáp.")
    print("         Hãy rút cáp USB của ESP32 từ PC cắm sang cổng USB của Raspberry Pi!")

# ------------------------------------------------------------------------------
# [5/6] Kiểm tra Chế độ Camera / GPS_ONLY
# ------------------------------------------------------------------------------
print("\n[5/6] 📷 KIỂM TRA CẤU HÌNH CAMERA & CHẾ ĐỘ VẬN HÀNH:")
gps_only = os.getenv("GPS_ONLY", "0") == "1" or os.getenv("DISABLE_CAMERA", "0") == "1"
if gps_only:
    print("  • Chế độ hiện tại      : [OK] GPS_ONLY=1 (Đã ngắt Camera & AI chủ động để ưu tiên test GPS + ESP32)")
else:
    try:
        from AI.scripts.camera_config import ensure_pi_eth_camera_route, open_camera, resolve_camera_target
        ensure_pi_eth_camera_route()
        for eth_name in ("eth0", "end0"):
            carrier_file = Path(f"/sys/class/net/{eth_name}/carrier")
            if carrier_file.exists():
                try:
                    carrier_val = carrier_file.read_text().strip()
                except Exception:
                    carrier_val = "0"
                if carrier_val == "1":
                    print(f"  • Cổng LAN RJ45 ({eth_name}) : [OK] Đã nhận tín hiệu cáp mạng vật lý (Link UP -> 192.168.1.210)")
                else:
                    print(f"  • Cổng LAN RJ45 ({eth_name}) : [!] Chưa có tín hiệu cáp LAN (Kiểm tra nguồn 12V của Camera hoặc dây RJ45)")
        target = resolve_camera_target()
        print(f"  • Nguồn Camera cấu hình: {target}")
        cap = open_camera()
        if cap is not None and cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                print(f"  • Thu hình Camera      : [OK] Thành công ({w}x{h})")
            else:
                print("  • Thu hình Camera      : [!] Mở được nhưng chưa có frame")
            cap.release()
        else:
            print("  • Thu hình Camera      : [⏳ CHỜ KẾT NỐI] Chưa bắt được RTSP (192.168.1.210) hoặc USB WebCam")
            print("    ↳ 💡 Ghi chú: Hệ thống sẽ tự động dò tìm liên tục đến khi bạn cắm/cấp nguồn camera.")
    except Exception as exc:
        print(f"  • Lỗi kiểm tra Camera  : {exc}")

# ------------------------------------------------------------------------------
# [6/6] Kiểm tra Thư viện Python trong venv của Pi
# ------------------------------------------------------------------------------
print("\n[6/6] 📦 KIỂM TRA THƯ VIỆN PYTHON TRONG MÔI TRƯỜNG ẢO (venv):")
for mod_name, label in (
    ("serial", "pyserial (Giao tiếp UART ESP32)"),
    ("psutil", "psutil (Giám sát CPU/RAM Pi)"),
    ("cv2", "opencv-python (Xử lý ảnh & Web)"),
    ("ultralytics", "ultralytics (YOLOv8)"),
    ("mediapipe", "mediapipe (FaceLandmarker)"),
):
    try:
        importlib.import_module(mod_name)
        print(f"  • {label:<34}: [OK] Sẵn sàng")
    except Exception as e:
        print(f"  • {label:<34}: [!] Chưa cài ({e})")

print("\n" + "=" * 74)
print("  ✅ HOÀN TẤT KIỂM TRA TRÊN RASPBERRY PI! ĐỂ CHẠY HỆ THỐNG:")
print("     bash AI/deploy/test_pi_web.sh")
print("=" * 74)
