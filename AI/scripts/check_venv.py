"""Script kiem tra toan ven moi truong Python ao (.venv) tren Raspberry Pi 4.

Chay truc tiep tren Terminal cua Pi:
    source .venv/bin/activate
    python AI/scripts/check_venv.py
"""

from __future__ import annotations

import sys
import importlib

print("=" * 65)
print(" KIEM TRA MOI TRUONG PYTHON (.venv) TREN RASPBERRY PI")
print("=" * 65)

in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
print(f"Trinh thuc thi Python: {sys.executable}")
print(f"Trang thai Virtualenv: {'[OK] DANG TRONG .venv' if in_venv else '[CANH BAO] CHUA KICH HOAT .venv (Chay: source .venv/bin/activate)'}\n")

# Danh sach cac goi thu vien can kiem tra
PACKAGES = [
    ("numpy", "numpy", "numpy>=1.26"),
    ("cv2", "OpenCV", "opencv-python"),
    ("serial", "PySerial", "pyserial>=3.5"),
    ("psutil", "psutil", "psutil>=5.9"),
    ("ultralytics", "Ultralytics YOLO", "ultralytics>=8.3"),
    ("mediapipe", "MediaPipe", "mediapipe>=0.10"),
]

missing_pkgs = []
system_fix_needed = False

print("--- Ket qua kiem tra tung thu vien ---")
for mod_name, display_name, install_name in PACKAGES:
    try:
        mod = importlib.import_module(mod_name)
        ver = getattr(mod, "__version__", "San sang")
        print(f"  [OK] {display_name:<18} : Da cai dat (Phien ban: {ver})")
    except ImportError as e:
        err_msg = str(e)
        print(f"  [X]  {display_name:<18} : THIEU hoac LOI ({err_msg})")
        missing_pkgs.append(install_name)
        if "libGL.so" in err_msg or "libgthread" in err_msg:
            system_fix_needed = True

print("\n" + "=" * 65)
if not missing_pkgs:
    print(" CHUC MUNG! MOI TRUONG PYTHON DA DAY DU 100%!")
    print(" Ban da co the khoi dong he thong:")
    print("   python -m AI.app.main")
else:
    print(f" PHAT HIEN {len(missing_pkgs)} THU VIEN CHUA TAI HOAC LOI DO ROT MANG:")
    for p in missing_pkgs:
        print(f"   - {p}")
    print("\n--- LENH CAI BU PHAN CON THIEU (Khong can tai lai tu dau) ---")
    if system_fix_needed:
        print("1. Cai thu vien C con thieu cua Linux:")
        print("   sudo apt install -y libgl1 libglib2.0-0\n")
    print("2. Chay lenh nay de cai dat tiep cac goi con thieu:")
    print(f"   pip install {' '.join(missing_pkgs)}")
print("=" * 65)
