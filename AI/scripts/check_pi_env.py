"""Script kiem tra nhanh moi truong phan cung Raspberry Pi 4 (IOTv3 On-Vehicle).

Chay truc tiep tren bo mach Pi:
    python AI/scripts/check_pi_env.py
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

print("=" * 68)
print(" KIEM TRA DIEU KIEN THUC CHIEN RASPBERRY PI 4 (IOTv3)")
print("=" * 68)

# 1. Kiem tra Nhiet do & Nguon dien SoC (Under-voltage)
print("\n[1/5] Kiem tra phan cung SoC & Nguon cap:")
temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
if temp_path.exists():
    try:
        temp_c = float(temp_path.read_text().strip()) / 1000.0
        status_temp = "(Binh thuong)" if temp_c < 75.0 else "(CANH BAO NONG)"
        print(f"  - Nhiet do SoC: {temp_c:.1f} °C {status_temp}")
    except Exception as e:
        print(f"  - Loi doc nhiet do: {e}")
else:
    print("  - Nhiet do: Khong truy van duoc (khong phai Linux Pi)")

try:
    throttled = subprocess.check_output(["vcgencmd", "get_throttled"], text=True).strip()
    code_hex = int(throttled.split("=")[-1], 16) if "=" in throttled else 0
    print(f"  - Trang thai Throttling: {throttled} (Hex: 0x{code_hex:X})")
    if code_hex & 0x1:
        print("    [!] CANH BAO: Nguon dien bi sut ap (Under-voltage)! Kiem tra tau sac xe.")
    if code_hex & 0x2:
        print("    [!] CANH BAO: CPU dang bi bop xung (Throttling) do qua nhiet.")
    if code_hex & 0x10000:
        print("    [i] Ghi nhan: Tung xay ra sut ap nguon trong qua trinh xe van hanh.")
    if code_hex == 0:
        print("    [OK] Nguon dien va xung nhip SoC on dinh 100%.")
except Exception:
    print("  - vcgencmd: Khong kha dung tren moi truong hien tai.")

# 2. Kiem tra quyen truy cap phan cung (Dialout, Video)
print("\n[2/5] Kiem tra phan quyen User:")
user = os.getenv("USER", "")
try:
    groups = subprocess.check_output(["groups", user], text=True)
    has_dialout = "dialout" in groups
    has_video = "video" in groups
    print(f"  - Quyen Serial UART (dialout): {'OK' if has_dialout else 'THIEU (Chay: sudo usermod -aG dialout $USER)'}")
    print(f"  - Quyen Camera (video):        {'OK' if has_video else 'THIEU (Chay: sudo usermod -aG video $USER)'}")
except Exception:
    print("  - Bo qua kiem tra groups tren Windows.")

# 3. Kiem tra Camera (LAN RTSP hoac USB)
print("\n[3/5] Kiem tra ket noi Camera:")
try:
    from AI.scripts.camera_config import open_camera, resolve_camera_target
    target = resolve_camera_target()
    print(f"  - Muc tieu Camera: {target}")
    cap = open_camera()
    if cap.isOpened():
        ok, frame = cap.read()
        if ok and frame is not None:
            h, w = frame.shape[:2]
            print(f"  - [OK] Thu hinh thanh cong: Do phan giai {w}x{h} | Kich thuoc {frame.nbytes / 1024:.1f} KB")
        else:
            print("  - [!] Camera mo duoc nhung chua nhan duoc frame.")
        cap.release()
    else:
        print("  - [X] KHONG THE MO CAMERA! Kiem tra day mang LAN hoac cap USB.")
except Exception as exc:
    print(f"  - Loi kiem tra camera: {exc}")

# 4. Kiem tra ESP32 UART & Loa
print("\n[4/5] Kiem tra ket noi ESP32 & Cong Serial:")
try:
    from AI.scripts.esp_communication import auto_detect_serial_port, EspCommunicator
    port = auto_detect_serial_port(os.getenv("ESP_PORT"))
    print(f"  - Cong Serial phat hien: {port or 'Khong tim thay'}")
    if port:
        esp = EspCommunicator(mode="SERIAL", port=port, require_ack=False)
        esp.send_status("NORMAL")
        time.sleep(0.3)
        print(f"  - Ket noi cong Serial ({port}): {'[OK] THANH CONG' if esp.connected else '[!] CHUA KET NOI'}")
        esp.close()
    else:
        print("  - [i] Tu dong chay ESP_MODE=MOCK neu chua cam cap USB ESP32.")
except Exception as exc:
    print(f"  - Loi kiem tra ESP32: {exc}")

# 5. Kiem tra Bo nao AI (YOLO & MediaPipe)
print("\n[5/5] Kiem tra Suy dien AI:")
repo_root = Path(__file__).resolve().parents[2]
yolo_path = repo_root / "AI" / "models" / "best.pt"
print(f"  - Mo hinh YOLO ({yolo_path.name}): {'TON TAI' if yolo_path.exists() else 'THIEU'}")
try:
    from ultralytics import YOLO
    model = YOLO(str(yolo_path))
    print("  - [OK] Ultralytics YOLO: San sang.")
except Exception as e:
    print(f"  - [X] Loi khoi dong YOLO: {e}")

try:
    import mediapipe as mp
    print("  - [OK] MediaPipe ARM64: San sang.")
except Exception as e:
    print(f"  - [i] MediaPipe ({e}); He thong se tu dong chay che do YOLO-only an toan.")

print("\n" + "=" * 68)
print(" HOAN TAT KIEM TRA PHAN CUNG IOTv3!")
print("=" * 68)
