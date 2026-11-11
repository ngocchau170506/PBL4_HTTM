"""Cầu nối trực tiếp Cổng COM3 (ESP32 + GPS cắm trên Máy Tính) sang Raspberry Pi qua mạng LAN.

Giải quyết bài toán:
- Mạch ESP32 + Cảm biến GPS đang cắm ở cổng COM3 trên Máy tính Windows.
- Chương trình chính lại đang chạy trên Raspberry Pi (192.168.1.17:8080).
- Script này đọc trực tiếp tín hiệu từ COM3 trên máy tính và bắn thẳng sang Pi (qua /api/esp_bridge),
  đồng thời nhận lệnh còi/loa từ Pi trả về để phát loa ngay trên mạch COM3!
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

repo_root = Path(__file__).resolve().parents[2]
for p in (str(repo_root), str(repo_root / "AI"), str(repo_root / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from AI.scripts.esp_communication import EspCommunicator


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Chuyển tiếp ESP32 + GPS từ COM3 Máy tính sang Raspberry Pi")
    parser.add_argument("--pi-url", type=str, default="http://192.168.1.17:8080", help="Địa chỉ Web Server đang chạy trên Pi")
    parser.add_argument("--port", type=str, default="COM3", help="Cổng COM của ESP32 trên máy tính")
    args = parser.parse_args()

    pi_bridge_url = args.pi_url.rstrip("/") + "/api/esp_bridge"

    print("=" * 72)
    print("  🔗 CẦU NỐI TÍN HIỆU ESP32 + GPS TỪ MÁY TÍNH (COM3) SANG RASPBERRY PI")
    print("=" * 72)
    print(f"  [CỔNG COM MÁY TÍNH] : {args.port}")
    print(f"  [ĐÍCH RASPBERRY PI] : {pi_bridge_url}")
    print("=" * 72)

    esp = EspCommunicator(mode="SERIAL", port=args.port)

    try:
        while True:
            raw = esp.latest_telemetry or ""
            payload = {
                "port": f"{esp.port or args.port} (PC -> Pi)",
                "latest_telemetry": raw,
                "reset_count": esp.reset_count,
            }

            if "{" in raw and "}" in raw:
                try:
                    jdata = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
                    payload.update(jdata)
                except Exception:
                    pass

            try:
                req = urllib.request.Request(
                    pi_bridge_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    if resp.status == 200:
                        reply = json.loads(resp.read().decode("utf-8"))
                        for cmd in reply.get("pending_commands", []):
                            print(f"[PI -> COM3] Thực thi lệnh từ Pi: {cmd}")
                            esp.send_raw_command(cmd, wait_ack=False)
            except Exception as exc:
                print(f"[ĐANG CHỜ PI] Chưa kết nối được {args.pi_url} ({exc})...")

            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[DỪNG] Đã ngắt cầu nối COM3 -> Pi.")
    finally:
        esp.close()


if __name__ == "__main__":
    main()
