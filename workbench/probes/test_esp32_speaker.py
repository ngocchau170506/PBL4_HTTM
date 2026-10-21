#!/usr/bin/env python3
"""Script kiểm tra kích hoạt trực tiếp loa/còi ESP32 qua cổng COM3 với các tần số và loại cảnh báo khác nhau.
"""
from __future__ import annotations

import sys
import time

try:
    import serial
except ImportError:
    print("LỖI: Chưa cài thư viện pyserial!")
    sys.exit(1)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    port = "COM3"
    baud = 115200

    print("=" * 70)
    print(" TIẾN HÀNH THỬ NGHIỆM TRỰC TIẾP CÁC CẢNH BÁO LOA TRÊN ESP32 (COM3)")
    print("=" * 70)

    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            timeout=1.0,
            write_timeout=1.0,
            dsrdtr=False,
            rtscts=False,
        )
    except Exception as exc:
        print(f"Không thể mở cổng {port}: {exc}")
        return 1

    time.sleep(1.0)
    # Xả bộ đệm nhận ban đầu
    ser.read_all()

    def send_cmd(cmd: str, wait_after: float = 1.0, desc: str = ""):
        print(f"\n[GỬI LỆNH] -> {cmd} ({desc})")
        ser.write((cmd + "\n").encode("utf-8"))
        ser.flush()
        t0 = time.time()
        while time.time() - t0 < wait_after:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if line:
                print(f"  [ESP32 TRẢ LỜI] {line}")
            time.sleep(0.05)

    # 1. Bắt tay kết nối
    send_cmd("CONNECT", wait_after=1.0, desc="Thiết lập liên kết với ESP32")

    # 2. Kiểm tra Cảnh báo Mức 1 (Tần số 600 Hz - tiếng bíp trầm vừa)
    send_cmd("ALERT:1:1500", wait_after=2.0, desc="Cảnh báo mức 1 (Tần số 600 Hz, thời gian 1.5s)")

    # 3. Kiểm tra Cảnh báo Mức 2 (Tăng tần số âm thanh lên 900 Hz - gắt hơn, nguy hiểm)
    send_cmd("ALERT:2:2000", wait_after=2.5, desc="Cảnh báo mức 2 (Tăng tần số lên 900 Hz, thời gian 2.0s)")

    # 4. Kiểm tra Cảnh báo Ngủ gật (Tần số cao kép 750 Hz & 1050 Hz nhịp nhanh)
    send_cmd("SLEEP", wait_after=3.0, desc="Cảnh báo ngủ gật (Tần số kép 750 Hz / 1050 Hz)")

    # 5. Tăng tần số phát lặp lại (Burst Cadence: phát nhịp dồn dập tần số cao 5 lần liên tiếp)
    print("\n>>> TĂNG TẦN SỐ PHÁT DỒN DẬP (BURST ALERT - Tần số 900 Hz lặp lại liên tục)...")
    for i in range(1, 6):
        send_cmd("ALERT:2:300", wait_after=0.45, desc=f"Nhịp phát dồn dập {i}/5")

    # 6. Kiểm tra các cảnh báo bằng giọng nói tiếng Việt
    send_cmd("VOICE:1", wait_after=3.0, desc="Giọng nói: Cảnh báo tài xế buồn ngủ")
    send_cmd("VOICE:2", wait_after=2.5, desc="Giọng nói: Cảnh báo ngáp")
    send_cmd("VOICE:3", wait_after=2.5, desc="Giọng nói: Cảnh báo mất tập trung")
    send_cmd("VOICE:4", wait_after=3.0, desc="Giọng nói: Cảnh báo nồng độ khí CO2 cao")

    # 7. Dừng âm thanh và đóng kết nối
    send_cmd("STOP", wait_after=0.5, desc="Dừng còi/loa")

    ser.close()
    print("\n" + "=" * 70)
    print(" ĐÃ HOÀN THÀNH TOÀN DIỆN THỬ NGHIỆM CÁC MỨC CẢNH BÁO TRÊN LOA ESP32!")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
