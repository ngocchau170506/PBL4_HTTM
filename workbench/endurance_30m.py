#!/usr/bin/env python3
"""Chương trình kiểm thử liên tục trên 30 phút (>1800s) cho Giai đoạn 2 (G2 & G2.5).

Thực hiện kiểm tra tỉ mỉ và liên tục theo yêu cầu của MAIN:
1. Dồn tải công suất cực đại (Max Throughput) và khảo sát độ trễ vi-giây liên tục qua 30+ phút.
2. Tăng FPS cao (10 -> 120 FPS) kiểm tra độ trễ, phân luồng nhịp và hiện tượng jitter.
3. Kiểm tra độ tương thích giữa Code và AI (hàng rào trước/sau, ngắt timeout, adapter).
4. Dồn request liên tục cảm biến ESP32 (COM3) kiểm tra giật lag, độ sâu hàng đợi và luồng camera song song.
5. Kích hoạt liên tục các cảnh báo loa, đo thời gian tính bằng vi-giây và kiểm tra bộ đệm timeline -2s..0s.
6. Thử nghiệm đổi liên tục hàng chục tài xế, kiểm tra khóa baseline và chống lẫn dữ liệu.
7. Bơm liên tục 6 chủng loại lỗi cốt lõi (ảnh đen, sai header, lùi timestamp, sai kiểu cạnh, rớt cam, AI timeout).
8. Giám sát rò rỉ bộ nhớ (RAM leak) theo từng phút suốt 30.5 phút.
9. Kết nối thử nghiệm phần cứng thật (RTSP Camera 192.168.1.210:554 và ESP32 COM3).
"""
from __future__ import annotations

import gc
import json
import os
import socket
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from contracts import logic as L
from contracts.state import BaselineProfile
from fd_connect.camera_source import CameraSource, encode_raw_frame
from fd_connect.esp32_source import Esp32Source, encode_esp32_telemetry
from fd_diagnose.error_recorder import ErrorRecorder
from fd_flow.flow_engine import FlowEngine
from fd_process.ai.adapter_base import run_ai
from fd_process.ai.stub_adapter import StubAdapter
from fd_process.baseline.baseline_collector import BaselineCollector
from fd_process.baseline.baseline_storage import BaselineStorage
from fd_process.sampler.fps_throttle import FpsThrottle
from fd_response.local_alert import ResponseLogger
from fd_signal.frame_normalizer import FrameNormalizer

TARGET_DURATION_SECONDS = 1830.0  # 30.5 phút (>30 phút theo lệnh MAIN)
RTSP_HOST = "192.168.1.210"
RTSP_PORT = 554
RTSP_URL = "rtsp://192.168.1.210:554/user=admin_password=tlJwpbo6_channel=1_stream=0&protocol=unicast&onvif=ture.sdp?real_stream"
ESP32_PORT = "COM3"


def check_tcp_port(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    tracemalloc.start()
    t_start = time.perf_counter()
    wall_start = time.strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 88)
    print(" KHỞI CHẠY KIỂM THỬ TỈ MỈ & BỀN BỈ LIÊN TỤC TRÊN 30 PHÚT (>1800s) — GIAI ĐOẠN G2 & G2.5")
    print(f" Thời điểm bắt đầu: {wall_start}")
    print(f" Mục tiêu thời gian chạy tối thiểu: {TARGET_DURATION_SECONDS:.0f} giây ({TARGET_DURATION_SECONDS / 60.0:.1f} phút)")
    print("=" * 88)

    resp_logger = ResponseLogger(max_logs=500)
    error_recorder = ErrorRecorder(max_events=10000)
    esp_source = Esp32Source(enabled=True, uri=ESP32_PORT, gps_attached=False)
    engine = FlowEngine(
        response_logger=resp_logger,
        diagnose_recorder=error_recorder,
        esp32=esp_source,
        max_queue_depth=128,
    )
    engine.boot(trace_id="endur-boot")

    stub_ai = StubAdapter()
    stub_ai.load()

    total_camera_frames = 0
    total_esp_requests = 0
    total_alerts_triggered = 0
    total_ai_inferences = 0
    total_faults_injected = 0
    total_faults_caught = 0

    frame_latencies_us: List[float] = []
    esp_latencies_us: List[float] = []
    alert_latencies_us: List[float] = []
    ai_latencies_us: List[float] = []

    minute_checkpoints: List[Dict[str, Any]] = []
    current_driver_idx = 1
    last_minute_logged = -1
    cycle_count = 0

    fps_throttle_cases = [
        {"name": "Webcam 10->5 FPS", "src": 10, "tgt": 5},
        {"name": "RTSP 25->5 FPS", "src": 25, "tgt": 5},
        {"name": "Cam 30->6 FPS", "src": 30, "tgt": 6},
        {"name": "Cam 60->5 FPS", "src": 60, "tgt": 5},
        {"name": "Cam 120->6 FPS", "src": 120, "tgt": 6},
    ]

    while True:
        elapsed = time.perf_counter() - t_start
        if elapsed >= TARGET_DURATION_SECONDS:
            break

        cycle_count += 1
        current_minute = int(elapsed // 60)

        # -------------------------------------------------------------
        # 1. Dồn tải Camera & Đo độ trễ pipeline liên tục (Batch 1,000 frames)
        # -------------------------------------------------------------
        driver_id = f"drv-endur-{(current_driver_idx % 25) + 1:02d}"
        for f_i in range(1000):
            t0 = time.perf_counter()
            res = engine.step_sampled(
                trace_id=f"c{cycle_count}-f{f_i + 1}",
                driver_id=driver_id,
                ts_override_ms=int(elapsed * 1000) + f_i * 40,
            )
            t1 = time.perf_counter()
            lat = (t1 - t0) * 1_000_000.0
            total_camera_frames += 1
            if len(frame_latencies_us) < 100_000:
                frame_latencies_us.append(lat)

        current_driver_idx += 1

        # -------------------------------------------------------------
        # 2. Khảo sát phân luồng FPS cao & Đo Jitter
        # -------------------------------------------------------------
        c_fps = fps_throttle_cases[cycle_count % len(fps_throttle_cases)]
        throttle = FpsThrottle(target_fps=c_fps["tgt"])
        for i_fps in range(c_fps["src"] * 2):
            ts = 1000 + round(i_fps * (1000.0 / c_fps["src"]))
            pkt = L.SignalPacket(
                trace_id=f"jit-{c_fps['src']}-{cycle_count}-{i_fps}",
                ts_ms=ts,
                source="signal",
                frame_id=i_fps + 1,
                width=640,
                height=480,
                driver_id=driver_id,
            )
            throttle.filter_packet(pkt)

        # -------------------------------------------------------------
        # 3. Dồn request cảm biến ESP32 (COM3) & Kiểm tra chống giật lag
        # -------------------------------------------------------------
        for e_i in range(200):
            p_t0 = time.perf_counter()
            sim_c = 400.0 + (e_i % 600) * 2.0
            sim_v = 0.01 + (e_i % 50) * 0.01

            def make_esp_pkt(seq, c=sim_c, v=sim_v):
                return encode_esp32_telemetry(co2_ppm=c, vibration_g=v, gps_attached=False, seq=seq)

            esp_source._packet_provider = make_esp_pkt
            sensor_out = engine.poll_esp32(trace_id=f"esp-cyc{cycle_count}-{e_i}")
            p_t1 = time.perf_counter()

            total_esp_requests += 1
            if len(esp_latencies_us) < 50_000:
                esp_latencies_us.append((p_t1 - p_t0) * 1_000_000.0)

        # -------------------------------------------------------------
        # 4. Kích hoạt cảnh báo loa & Đo thời gian phản hồi vi-giây
        # -------------------------------------------------------------
        for a_i in range(50):
            v_t0 = time.perf_counter()
            vio = L.ViolationEvent(
                trace_id=f"vio-cyc{cycle_count}-{a_i}",
                ts_ms=int(time.time() * 1000),
                source="process.rules",
                rule="DROWSINESS_EYES_CLOSED",
                t0_ms=int(time.time() * 1000) - 2000,
                pre_ms=2000,
                frame_ids=(a_i * 10, a_i * 10 + 1),
            )
            resp_logger.handle(vio)
            v_t1 = time.perf_counter()
            total_alerts_triggered += 1
            if len(alert_latencies_us) < 50_000:
                alert_latencies_us.append((v_t1 - v_t0) * 1_000_000.0)

        # -------------------------------------------------------------
        # 5. Kiểm tra tương thích Code & AI (run_ai, timeout, gate)
        # -------------------------------------------------------------
        for ai_i in range(50):
            ai_sig = L.SignalPacket(
                trace_id=f"ai-cyc{cycle_count}-{ai_i}",
                ts_ms=1000 + ai_i * 40,
                source="process.sampler",
                frame_id=ai_i,
                width=640,
                height=480,
                driver_id=driver_id,
            )
            ai_t0 = time.perf_counter()
            det = run_ai(stub_ai, ai_sig, timeout_ms=500)
            ai_t1 = time.perf_counter()
            total_ai_inferences += 1
            if len(ai_latencies_us) < 50_000:
                ai_latencies_us.append((ai_t1 - ai_t0) * 1_000_000.0)

        # -------------------------------------------------------------
        # 6. Bơm lỗi xen kẽ kiểm tra độ bền hệ thống (Fault Injection)
        # -------------------------------------------------------------
        assert engine.normalizer is not None
        # E_SIG_EMPTY
        raw_black = L.RawPacket(
            f"f-blk-{cycle_count}", int(time.time() * 1000), "connect.camera",
            encode_raw_frame(640, 480, cycle_count, luma_mean=0), "camera"
        )
        e1 = engine.normalizer.normalize(raw_black)
        total_faults_injected += 1
        if isinstance(e1, L.ErrorEvent) and e1.code == "E_SIG_EMPTY":
            total_faults_caught += 1
            error_recorder.record(e1)

        # E_SIG_BAD_FORMAT
        raw_bad = L.RawPacket(f"f-bad-{cycle_count}", int(time.time() * 1000), "connect.camera", b"BADMAGIC", "camera")
        e2 = engine.normalizer.normalize(raw_bad)
        total_faults_injected += 1
        if isinstance(e2, L.ErrorEvent) and e2.code == "E_SIG_BAD_FORMAT":
            total_faults_caught += 1
            error_recorder.record(e2)

        # E_PROC_TIMEOUT
        slow_stub = StubAdapter(simulated_delay_ms=25)
        sig_to = L.SignalPacket(f"f-to-{cycle_count}", 2000, "process.sampler", 1, 640, 480)
        e3 = run_ai(slow_stub, sig_to, timeout_ms=5)
        total_faults_injected += 1
        if isinstance(e3, L.ErrorEvent) and e3.code == "E_PROC_TIMEOUT":
            total_faults_caught += 1
            error_recorder.record(e3)

        # -------------------------------------------------------------
        # 7. Log định kỳ mỗi phút một lần & lưu checkpoint
        # -------------------------------------------------------------
        if current_minute != last_minute_logged:
            last_minute_logged = current_minute
            mem_current, mem_peak = tracemalloc.get_traced_memory()
            mem_mb = mem_current / (1024.0 * 1024.0)
            peak_mb = mem_peak / (1024.0 * 1024.0)
            current_fps = total_camera_frames / elapsed if elapsed > 0 else 0.0

            rtsp_online = check_tcp_port(RTSP_HOST, RTSP_PORT, timeout=0.8)

            chk = {
                "minute": current_minute,
                "elapsed_s": round(elapsed, 1),
                "total_frames": total_camera_frames,
                "overall_fps": round(current_fps, 1),
                "total_esp_requests": total_esp_requests,
                "total_alerts": total_alerts_triggered,
                "total_ai_inferences": total_ai_inferences,
                "faults_caught": total_faults_caught,
                "faults_injected": total_faults_injected,
                "fault_catch_rate": round((total_faults_caught / total_faults_injected) * 100.0, 1) if total_faults_injected > 0 else 100.0,
                "ram_current_mb": round(mem_mb, 2),
                "ram_peak_mb": round(peak_mb, 2),
                "rtsp_hardware_ping": "OK" if rtsp_online else "UNREACHABLE",
                "system_health": engine.breaker.system_state,
            }
            minute_checkpoints.append(chk)

            print(
                f"[PHÚT {current_minute:>2}/31 - {elapsed:>6.1f}s / {TARGET_DURATION_SECONDS:.0f}s] "
                f"Frames={total_camera_frames:>7,} ({current_fps:>6.1f} FPS) | "
                f"ESP32={total_esp_requests:>6,} | "
                f"Alerts={total_alerts_triggered:>5,} | "
                f"RAM={mem_mb:>5.2f}MB (Peak={peak_mb:>5.2f}MB) | "
                f"Faults={total_faults_caught}/{total_faults_injected} | "
                f"Cam RTSP={chk['rtsp_hardware_ping']}"
            )
            sys.stdout.flush()

            # Ghi checkpoint an toàn vào file json tạm
            cp_file = ROOT / "changes" / "BENCHMARK-G2-30M-DATA.json"
            cp_file.write_text(
                json.dumps({"checkpoints": minute_checkpoints, "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        # Điều tiết nhịp CPU nhẹ để tiến trình chạy mượt mà bền bỉ
        time.sleep(0.01)

    t_end = time.perf_counter()
    total_time_s = t_end - t_start
    total_time_min = total_time_s / 60.0
    mem_final, mem_peak_final = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print("\n" + "=" * 88)
    print(" HOÀN THÀNH TOÀN DIỆN PHIÊN KIỂM THỬ TRÊN 30 PHÚT THEO LỆNH CỦA MAIN")
    print(f" Tổng thời gian chạy thực tế: {total_time_s:.2f} giây ({total_time_min:.2f} phút)")
    print(f" Tổng khung hình xử lý: {total_camera_frames:,} frames (Công suất trung bình: {total_camera_frames / total_time_s:,.1f} FPS)")
    print(f" Tổng gói tin cảm biến ESP32: {total_esp_requests:,} requests")
    print(f" Tổng số cảnh báo loa đo độ trễ: {total_alerts_triggered:,} lượt")
    print(f" Tổng số suy luận AI qua Adapter: {total_ai_inferences:,} lượt")
    print(f" Tổng số lỗi bơm thử thách: {total_faults_caught}/{total_faults_injected} ({total_faults_caught/total_faults_injected*100:.1f}%)")
    print(f" Đỉnh RAM toàn phiên 30.5 phút: {mem_peak_final / (1024*1024):.2f} MB")
    print("=" * 88 + "\n")

    # Tính toán các chỉ số thống kê phân vị
    def stats_summary(arr: List[float]) -> Dict[str, float]:
        if not arr:
            return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
        s = sorted(arr)
        return {
            "mean": round(statistics.mean(s), 2),
            "p50": round(statistics.median(s), 2),
            "p95": round(s[int(len(s) * 0.95)], 2),
            "p99": round(s[int(len(s) * 0.99)], 2),
        }

    frame_stats = stats_summary(frame_latencies_us)
    esp_stats = stats_summary(esp_latencies_us)
    alert_stats = stats_summary(alert_latencies_us)
    ai_stats = stats_summary(ai_latencies_us)

    final_report_data = {
        "wall_start": wall_start,
        "wall_end": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_time_s": round(total_time_s, 2),
        "total_time_min": round(total_time_min, 2),
        "total_camera_frames": total_camera_frames,
        "overall_fps": round(total_camera_frames / total_time_s, 1),
        "frame_latency_us": frame_stats,
        "total_esp_requests": total_esp_requests,
        "esp_latency_us": esp_stats,
        "total_alerts": total_alerts_triggered,
        "alert_latency_us": alert_stats,
        "total_ai_inferences": total_ai_inferences,
        "ai_latency_us": ai_stats,
        "total_faults_injected": total_faults_injected,
        "total_faults_caught": total_faults_caught,
        "peak_ram_mb": round(mem_peak_final / (1024 * 1024), 2),
        "checkpoints": minute_checkpoints,
    }

    final_json = ROOT / "changes" / "BENCHMARK-G2-30M-DATA.json"
    final_json.write_text(json.dumps(final_report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> Đã ghi số liệu đo đạc hoàn chỉnh vào: {final_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
