"""Script kiểm tra và thu thập thông tin Camera Ngoại Vantech IP (RTSP).

Chức năng:
1. Xuất thông tin xác thực (URL, Mật khẩu, Tài khoản, Cổng kết nối RTSP).
2. Kiểm tra quy tắc bảo vệ: Tuyệt đối KHÔNG chạy webcam máy tính (index 0).
3. Chạy chế độ chờ liên tục cho đến khi camera ngoại được cắm/kết nối thì DỪNG chờ và báo thành công.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
for p in (str(repo_root), str(repo_root / "AI"), str(repo_root / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from AI.scripts.camera_config import (
    get_camera_credentials,
    resolve_camera_target,
    open_camera,
    wait_for_external_camera,
)
import cv2


def main():
    parser = argparse.ArgumentParser(description="Kiểm tra kết nối Camera Ngoại")
    parser.add_argument("--once", action="store_true", help="Chỉ thử 1 lần rồi thoát (không lặp vô tận)")
    parser.add_argument("--max-attempts", type=int, default=0, help="Số lần thử tối đa (0 = lặp vô hạn)")
    args = parser.parse_args()

    creds = get_camera_credentials()

    print("=" * 70)
    print("      THÔNG TIN CẤU HÌNH & XÁC THỰC CAMERA NGOẠI VANTECH")
    print("=" * 70)
    print(f" -> Tên đăng nhập (User):     {creds['user']}")
    print(f" -> Mật khẩu camera (Pass):   {creds['password']}")
    print(f" -> Địa chỉ IP camera:        {creds['default_ip']}")
    print(f" -> Cổng RTSP mạng LAN:       {creds['default_port']}")
    print(f" -> URL RTSP Chính:           {creds['primary_url']}")
    print(f" -> URL RTSP Dự Phòng (Onvif):{creds['candidate_urls'][1]}")
    print(f" -> URL RTSP Sub-stream:      {creds['candidate_urls'][2]}")
    print("-" * 70)
    print(" [NOTE BẢO VỆ]:")
    print("  - TUYỆT ĐỐI KHÔNG mở webcam máy tính (device 0).")
    print("  - NẾU CHƯA BẮT ĐƯỢC: Hệ thống sẽ lặp lại chờ đợi đến khi bắt được thì DỪNG.")
    print("=" * 70)

    if args.once:
        print("\n[KIỂM TRA ĐƠN LẺ]: Thử mở camera ngoại 1 lần...")
        cap = open_camera()
        if cap is not None and cap.isOpened():
            print("[THÀNH CÔNG] Đã bắt được camera ngoại!")
            cap.release()
        else:
            print("[CHƯA KẾT NỐI] Chưa phát hiện camera ngoại tại địa chỉ trên. (Đã chặn webcam máy tính).")
        return

    # Chế độ đợi liên tục đến khi bắt được camera ngoại
    print("\n[BẮT ĐẦU CHẾ ĐỘ CHỜ CAMERA NGOẠI]...")
    try:
        cap = wait_for_external_camera(retry_interval=2.0)
        ok, frame = cap.read()
        if ok and frame is not None:
            h, w = frame.shape[:2]
            print(f"[KIỂM TRA FRAME] Nhận khung hình kích thước: {w}x{h} px thành công.")
        cap.release()
        print("[HOÀN TẤT] Đã bắt được camera ngoại và dừng vòng lặp chờ.")
    except KeyboardInterrupt:
        print("\n[ĐÃ DỪNG] Người dùng đã ấn dừng vòng lặp chờ.")


if __name__ == "__main__":
    main()
