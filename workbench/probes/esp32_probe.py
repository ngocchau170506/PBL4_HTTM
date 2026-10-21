"""Probe kiểm thử nhánh ESP32 cổng COM3 chưa lắp GPS (G2.5 / FEAT-025).

Chạy: python workbench/probes/esp32_probe.py
Kiểm tra:
  1. Mở kết nối ESP32 trên COM3 ở chế độ chưa gắn GPS (gps_attached=False).
  2. Đọc gói cảm biến qua cạnh optional (connect.esp32 -> signal) và ghép vào SignalPacket.sensor.
  3. Hệ thống ghi nhận cảnh báo thiếu GPS tại node_path='connect.esp32', chuyển sang DEGRADED.
  4. Ngắt kết nối ESP32 đột ngột -> luồng Camera + Sampler + Baseline + AI vẫn chạy thông suốt 100%.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L
from fd_connect.esp32_source import Esp32Source
from fd_flow.flow_engine import FlowEngine


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    esp = Esp32Source(enabled=True, uri="COM3", gps_attached=False)
    engine = FlowEngine(esp32=esp)
    boot_err = engine.boot(trace_id="g25-probe-boot")
    if boot_err is not None:
        print(f"FAIL boot: {boot_err}")
        return 1

    sensor = engine.poll_esp32(trace_id="g25-esp-poll-1")
    if not isinstance(sensor, dict) or sensor.get("gps_ready") != 0.0:
        print(f"FAIL poll_esp32: {sensor}")
        return 1

    if engine.breaker.system_state != "DEGRADED" or engine.health() != "DEGRADED":
        print(f"FAIL: Hệ thống không chuyển sang DEGRADED khi ESP32 thiếu GPS: {engine.breaker.system_state}")
        return 1

    print(
        f"[ESP32 G2.5 PROBE] Cổng {esp.uri}: nhận cảm biến {sensor} | "
        f"chưa lắp GPS -> trạng thái={engine.breaker.system_state} (ghi vào fd_diagnose)"
    )

    esp.simulate_disconnect()
    lost_evt = engine.poll_esp32(trace_id="g25-esp-lost")
    if not isinstance(lost_evt, L.ErrorEvent) or lost_evt.code != "E_CONN_LOST" or lost_evt.node_path != "connect.esp32":
        print(f"FAIL: Ngắt ESP32 không báo đúng E_CONN_LOST tại connect.esp32: {lost_evt}")
        return 1

    cam_passed = 0
    for i in range(50):
        res = engine.step_sampled(
            trace_id=f"g25-cam-after-esp-lost-{i + 1}",
            driver_id="drv-g25",
            ts_override_ms=2000 + i * 40,
        )
        if isinstance(res, L.ErrorEvent):
            print(f"FAIL: Luồng camera bị ảnh hưởng khi ESP32 lỗi: {res}")
            return 1
        if res is not None:
            cam_passed += 1

    if cam_passed != 10:
        print(f"FAIL: Luồng camera không lọc đúng 10 frame: {cam_passed}")
        return 1

    print(
        f"[ESP32 G2.5 PROBE] Ngắt ESP32 ({lost_evt.code} @ {lost_evt.node_path}) -> "
        f"Luồng Camera RTSP vẫn chạy mượt 50 frame (qua AI={cam_passed}/10 frame) -> PASS"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
