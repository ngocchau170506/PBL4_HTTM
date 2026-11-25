"""PC Bench Verification Tool for Real ESP32 #2 (COMMS_BLE_WIFI_HUB) over USB Serial.

Usage (after flashing firmware/esp32_ble_wifi_hub/esp32_ble_wifi_hub.ino via Arduino IDE):
  python contracts/test_esp2_bench.py --list-ports
  python contracts/test_esp2_bench.py --port COM5
  python contracts/test_esp2_bench.py --port COM5 --watch-ble 30
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pi_edge.comms.esp_ble_client import EspBleHubClient  # noqa: E402


def list_serial_ports() -> int:
    try:
        from serial.tools import list_ports
    except Exception as exc:
        print(f"[ERROR] pyserial chua duoc cai dat: {exc}")
        return 1
    ports = list(list_ports.comports())
    if not ports:
        print("Khong tim thay cong COM/USB serial nao.")
        return 0
    for p in ports:
        print(f"  {p.device:10s} | {p.description} | hwid={p.hwid}")
    return 0


def run_bench(port: str, baud: int, watch_ble_s: float) -> int:
    ble_events = []
    client = EspBleHubClient(
        port=port,
        baudrate=baud,
        mode="SERIAL",
        on_ble_action=lambda act: (ble_events.append(act), print(f"  [BLE_ACTION] {act}")),
        on_ble_state=lambda up: print(f"  [BLE_STATE] phone_connected={up}"),
    )
    if not client.start():
        print(f"[FAIL] Khong mo duoc cong {port} @ {baud}. Kiem tra Arduino Serial Monitor da dong chua!")
        return 1

    passed = 0
    failed = 0

    def _check(name: str, ok: bool, detail: str) -> None:
        nonlocal passed, failed
        tag = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{tag}] {name}: {detail}")

    try:
        # Allow brief cold-boot window if USB bridge pulsed reset
        time.sleep(0.4)
        r_ping = None
        for _ in range(3):
            r_ping = client.send_command("PING", wait_ack=True, timeout_s=0.9)
            if r_ping.ok:
                break
            time.sleep(0.6)
        _check("1. PING -> PONG", bool(r_ping and r_ping.ok), f"{r_ping.ack_line or r_ping.error} ({r_ping.latency_ms}ms)")

        r_who = client.send_command("WHOAMI", wait_ack=True, timeout_s=0.9)
        _check(
            "2. WHOAMI role",
            r_who.ok and client.device_role in ("COMMS_BLE_GPS_HUB", "COMMS_BLE_WIFI_HUB"),
            f"role={client.device_role}, raw={r_who.ack_line or r_who.error}",
        )

        r_hb = client.send_command('HB:{"co2_ppm":512.0,"speed_kmh":0.0,"gps_valid":false}', wait_ack=True, timeout_s=0.9)
        _check("3. Heartbeat HB:{...}", r_hb.ok, f"{r_hb.ack_line or r_hb.error} ({r_hb.latency_ms}ms)")

        r_not = client.send_command("NOTIFY:TEST_PI_AI_BENCH", wait_ack=True, timeout_s=0.9)
        _check("4. BLE NOTIFY", r_not.ok, f"{r_not.ack_line or r_not.error}")

        r_bad = client.send_command("CFG_SERVER:ftp://invalid", wait_ack=True, timeout_s=0.9)
        _check("5. Reject invalid URL (NACK)", (not r_bad.ok and r_bad.error == "ESP32_HUB_NACK"), f"{r_bad.ack_line or r_bad.error}")

        r_st = client.send_command("STATUS", wait_ack=True, timeout_s=1.0)
        _check(
            "6. STATUS JSON",
            r_st.ok and client.latest_status.get("role") in ("COMMS_BLE_GPS_HUB", "COMMS_BLE_WIFI_HUB"),
            json.dumps(client.latest_status, ensure_ascii=False),
        )

        if watch_ble_s > 0:
            print(f"\n[BLE WATCH] Dang cho {int(watch_ble_s)}s — hay mo app nRF Connect / Serial Bluetooth Terminal, ket noi 'PI_AI_HUB_...' va gui 'MUTE' hoac 'SOS'...")
            end_t = time.monotonic() + float(watch_ble_s)
            while time.monotonic() < end_t:
                client.send_heartbeat_if_due({"co2_ppm": 450.0, "gps": {"is_valid": False}}, interval_s=1.5)
                time.sleep(0.2)
            print(f"[BLE WATCH DONE] Nhan duoc {len(ble_events)} hanh dong BLE: {ble_events}")
    finally:
        client.close()

    print(f"\n=== ESP32 #2 BENCH SUMMARY: {passed} PASS / {failed} FAIL ===")
    return 0 if failed == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="PC Serial Bench Test for PI_AI ESP32 #2 (COMMS_BLE_WIFI_HUB)")
    ap.add_argument("--list-ports", action="store_true", help="List available COM/USB serial ports")
    ap.add_argument("--port", default="", help="Serial port (e.g. COM5 or /dev/ttyUSB1)")
    ap.add_argument("--baud", type=int, default=115200, help="Baudrate (default 115200)")
    ap.add_argument("--watch-ble", type=float, default=0.0, help="Wait N seconds to observe live BLE commands from phone")
    args = ap.parse_args()

    if args.list_ports or not args.port:
        return list_serial_ports()
    return run_bench(args.port, args.baud, args.watch_ble)


if __name__ == "__main__":
    raise SystemExit(main())
