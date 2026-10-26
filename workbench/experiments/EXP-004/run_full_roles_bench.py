#!/usr/bin/env python3
"""Runner kiem thu toan dien EXP-004: Camera 60 FPS + Hybrid Two-Stage AI + ESP32 G4.

Chay tren may tinh cua MAIN:
    python workbench/experiments/EXP-004/run_full_roles_bench.py

Bao cao dong bo tat ca cac vai tro:
  - [BRAIN] : Kiem chung hop dong src/contracts/ (logic.py, state.py) khong bi thay doi.
  - [CAM]   : Nguon Camera 60 FPS (16.67ms/frame), do jitter va luu khung hinh mau.
  - [ESP]   : Luong Telemetry 10Hz (CO2, rung, GPS) + Kenh lenh 2 chieu ALERT/ACK.
  - [AI]    : Hybrid Two-Stage (YOLO + MediaPipe on-demand, EAR/MAR Z-Score, 3D Head Pose).
  - [RULES] : RulesEngine (EyeDrowsiness, MouthYawn) chong chop mat gia & conflict.
  - [RESP]  : FrameRingBuffer (-2s clip) + ServerDispatcher (Outbox/Spool).
  - [G5-CALC]: Tinh toan tai CPU/RAM va nguong danh gia chuan bi cho G5.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time
import tracemalloc
from typing import Dict, List

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(EXP_DIR))

from contracts import logic as L
from fd_connect.camera_source import CameraSource
from fd_connect.esp32_commander import AckReceipt, AlertCommand, Esp32Commander
from fd_connect.esp32_source import Esp32Source
from fd_connect.sensor_replay import SensorReplay
from fd_flow.flow_engine import FlowEngine
from fd_process.sampler.fps_throttle import FpsThrottle
from fd_response.ring_buffer import FrameRingBuffer
from fd_response.server_dispatcher import DispatchPackage, ServerDispatcher
from fd_signal.frame_normalizer import FrameNormalizer

from hybrid_ai_adapter import FrameScenarioInput, HybridTwoStageAdapter
from mock_camera_60fps import Camera60FpsRig


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_10s_60fps_scenario(total_frames: int = 600) -> Dict[int, FrameScenarioInput]:
    """Xay dung kich ban 10 giay o 60 FPS (600 frames, moi frame 16.67ms):
    - Frame 1..75    (0.00s - 1.25s): Tai xe tinh tao (EAR=0.31, MAR=0.22).
    - Frame 76..255  (1.25s - 4.25s): Nham mat that su 3.0s (YOLO=Eyeclosed, EAR=0.14 -> CONFIRMED).
    - Frame 256..270 (4.25s - 4.50s): Tai xe mo mat tinh lai (EAR=0.31).
    - Frame 271..390 (4.50s - 6.50s): Bao dong gia YOLO 2.0s (YOLO=Eyeclosed nhung EAR=0.31 -> CONFLICT).
    - Frame 391..600 (6.50s - 10.0s): Ngap that su 3.5s (YOLO=Yawn, MAR=0.62 -> CONFIRMED).
    """
    scenario: Dict[int, FrameScenarioInput] = {}
    for fid in range(1, total_frames + 1):
        if fid <= 75 or (256 <= fid <= 270):
            scenario[fid] = FrameScenarioInput(
                yolo_label="Attentive eye",
                yolo_conf=0.95,
                target_ear=0.31,
                target_mar=0.22,
                yaw_deg=2.0,
                pitch_deg=-1.0,
            )
        elif fid <= 255:
            scenario[fid] = FrameScenarioInput(
                yolo_label="Eyeclosed",
                yolo_conf=0.91,
                target_ear=0.14,
                target_mar=0.22,
                yaw_deg=3.0,
                pitch_deg=18.0,
            )
        elif fid <= 390:
            # Tinh huong CONFLICT: YOLO nham la nham mat, nhung MediaPipe do EAR=0.31 (mat mo)
            scenario[fid] = FrameScenarioInput(
                yolo_label="Eyeclosed",
                yolo_conf=0.82,
                target_ear=0.31,
                target_mar=0.22,
                yaw_deg=5.0,
                pitch_deg=0.0,
            )
        else:
            scenario[fid] = FrameScenarioInput(
                yolo_label="Yawn",
                yolo_conf=0.93,
                target_ear=0.29,
                target_mar=0.62,
                yaw_deg=1.0,
                pitch_deg=5.0,
            )
    return scenario


def run_experiment() -> dict:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    logic_path = ROOT / "src" / "contracts" / "logic.py"
    state_path = ROOT / "src" / "contracts" / "state.py"
    hash_before = {
        "logic.py": sha256_file(logic_path),
        "state.py": sha256_file(state_path),
    }

    cfg = json.loads((EXP_DIR / "config_exp004.json").read_text(encoding="utf-8"))
    capture_dir = EXP_DIR / "captured_frames"

    tracemalloc.start()
    t0_bench = time.perf_counter()

    # 1. Khoi tao vai tro [CAM] 60 FPS
    cam_rig = Camera60FpsRig(
        width=cfg["camera"]["width"],
        height=cfg["camera"]["height"],
        target_fps=cfg["camera"]["target_capture_fps"],
        capture_dir=capture_dir,
    )
    simulated_clock_ms = {"now": 1000}
    cam_source = CameraSource(
        source="webcam",
        uri="mock://cam60fps",
        width=640,
        height=480,
        fps=60,
        frame_provider=lambda seq: cam_rig.next_raw_payload(seq, simulated_clock_ms["now"]),
    )

    # 2. Khoi tao vai tro [ESP] (Replay 10Hz + Commander 2 chieu)
    fixture_path = ROOT / "workbench" / "fixtures" / "telemetry_sample.jsonl"
    replay = SensorReplay(fixture_path=fixture_path, loop=True)
    esp_source = Esp32Source(
        enabled=True,
        uri=cfg["esp32"]["port"],
        baudrate=cfg["esp32"]["baudrate"],
        gps_attached=True,
        dsrdtr=False,
        rtscts=False,
        packet_provider=replay.next_packet_payload,
    )
    esp_commander = Esp32Commander(
        enabled=True,
        ack_timeout_ms=cfg["esp32"]["ack_timeout_ms"],
    )

    # 3. Khoi tao vai tro [AI] Hybrid Two-Stage (YOLO + MediaPipe on-demand)
    scenario_600 = build_10s_60fps_scenario(600)
    hybrid_ai = HybridTwoStageAdapter(
        ear_mean=cfg["driver_baseline"]["ear_mean"],
        ear_std=cfg["driver_baseline"]["ear_std"],
        mar_mean=cfg["driver_baseline"]["mar_mean"],
        mar_std=cfg["driver_baseline"]["mar_std"],
        scenario_provider=scenario_600,
    )

    # 4. Khoi tao [RESP] (FrameRingBuffer 120 frames cho 2s @ 60FPS + ServerDispatcher)
    ring_buffer = FrameRingBuffer(capacity=120, pre_event_ms=2000)
    dispatched_packages: List[dict] = []

    def mock_server_transport(pkg: DispatchPackage, timeout_ms: int):
        dispatched_packages.append(json.loads(pkg.to_manifest_json()))
        return True, "ACK-EXP004-OK"

    server_dispatcher = ServerDispatcher(
        transport=mock_server_transport,
        max_outbox=32,
        max_spool=128,
    )

    # 5. Khoi tao FlowEngine voi FpsThrottle (60 FPS -> 8 FPS cho AI)
    throttle_8fps = FpsThrottle(target_fps=cfg["sampler"]["ai_target_fps"])
    engine = FlowEngine(
        camera=cam_source,
        normalizer=FrameNormalizer(expected_width=640, expected_height=480),
        sampler=throttle_8fps,
        ai_adapter=hybrid_ai,
        esp32=esp_source,
        ring_buffer=ring_buffer,
        server_dispatcher=server_dispatcher,
        enable_rules_from_config=True,
    )

    boot_err = engine.boot(trace_id="exp004-boot")
    if boot_err is not None:
        raise RuntimeError(f"Boot failed: {boot_err}")

    # --- CHAY KICH BAN 1: 600 FRAMES (10s @ 60 FPS) CO PHAN LUONG ---
    frame_latencies_ms: List[float] = []
    esp_alerts_sent: List[AckReceipt] = []
    violations_detected: List[L.ViolationEvent] = []
    role_timeline_logs: List[str] = []

    for i in range(1, 601):
        # Moi frame cach nhau ~16.67ms (60 FPS)
        simulated_clock_ms["now"] = 1000 + int(round(i * (1000.0 / 60.0)))
        t_step0 = time.perf_counter()

        # Moi 6 frame camera (~100ms = 10Hz), doc 1 goi ESP32 telemetry
        if i % 6 == 0:
            engine.poll_esp32(trace_id=f"exp004-esp-{i}")

        res = engine.step_sampled(
            trace_id=f"exp004-f-{i:04d}",
            driver_id="drv-exp004",
            ts_override_ms=simulated_clock_ms["now"],
        )
        t_step_ms = (time.perf_counter() - t_step0) * 1000.0
        frame_latencies_ms.append(t_step_ms)

        # Luu anh mau tai cac moc quan trong (Binh thuong, Nham mat, Conflict, Ngap)
        if i in (60, 240, 360, 540):
            st_lbl = (
                "DROWSY" if i == 240 else ("CONFLICT" if i == 360 else ("YAWNING" if i == 540 else "NORMAL"))
            )
            cam_rig.save_sample_frame_pgm(i, st_lbl, hybrid_ai.last_ear, hybrid_ai.last_mar)

        while server_dispatcher.outbox_size > 0:
            flush_res = server_dispatcher.flush_one()
            if flush_res is not None and not isinstance(flush_res, L.ErrorEvent):
                violations_detected.append(flush_res)
                status_code = "DROWSY" if "eye" in flush_res.rule else "YAWNING"
                ack = esp_commander.send_alert(
                    AlertCommand(
                        status=status_code,
                        duration_ms=1500,
                        level=2 if status_code == "DROWSY" else 1,
                        trace_id=flush_res.trace_id,
                    )
                )
                if isinstance(ack, AckReceipt):
                    esp_alerts_sent.append(ack)

        if i in (60, 150, 240, 360, 520, 600):
            sec = i / 60.0
            role_timeline_logs.append(
                f"t={sec:4.1f}s (frame={i:3d}) | CAM=60FPS | ESP=OK(seq={esp_source.packet_count}) "
                f"| AI_drowsy={hybrid_ai.drowsy_state.value:<9} (EAR={hybrid_ai.last_ear:.2f}, Z={hybrid_ai.last_z_ear:+.1f}) "
                f"| AI_yawn={hybrid_ai.yawning_state.value:<9} (MAR={hybrid_ai.last_mar:.2f}) "
                f"| VIOLATIONS={len(violations_detected)} | ESP_ACK={esp_commander.total_acked}"
            )

    passed_10s = throttle_8fps.total_passed
    dropped_10s = throttle_8fps.total_dropped

    # --- CHAY KICH BAN 2: DO CONG SUAT CUC DAI 6,000 FRAMES (100 GIAY VIDEO 60 FPS) ---
    t_stress0 = time.perf_counter()
    stress_frames = 6000
    for s_idx in range(1, stress_frames + 1):
        simulated_clock_ms["now"] += 17
        engine.step_sampled(
            trace_id=f"stress-{s_idx}",
            driver_id="drv-exp004",
            ts_override_ms=simulated_clock_ms["now"],
        )
    stress_elapsed_s = time.perf_counter() - t_stress0
    max_throughput_fps = stress_frames / stress_elapsed_s if stress_elapsed_s > 0 else 0.0

    ram_current, ram_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    total_elapsed_s = time.perf_counter() - t0_bench

    hash_after = {
        "logic.py": sha256_file(logic_path),
        "state.py": sha256_file(state_path),
    }
    brain_unchanged = hash_before == hash_after

    sorted_lat = sorted(frame_latencies_ms)
    avg_lat = sum(sorted_lat) / len(sorted_lat)
    p50_lat = sorted_lat[len(sorted_lat) // 2]
    p95_lat = sorted_lat[int(len(sorted_lat) * 0.95)]
    p99_lat = sorted_lat[int(len(sorted_lat) * 0.99)]

    # Tinh toan du bao cho G5 & G6 (Raspberry Pi)
    # Gia su tren Raspberry Pi 4: YOLO mat ~45ms/frame, MediaPipe mat ~18ms/frame
    rpi_yolo_ms = 45.0
    rpi_mp_ms = 18.0
    unthrottled_60fps_load_pct = ((60.0 * (rpi_yolo_ms + rpi_mp_ms)) / 1000.0) * 100.0
    throttled_8fps_two_stage_load_pct = (
        (8.0 * rpi_yolo_ms + 8.0 * (hybrid_ai.mediapipe_activation_ratio / 100.0) * rpi_mp_ms) / 1000.0
    ) * 100.0

    report = {
        "experiment": "EXP-004",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "brain_contracts_verified": {
            "unchanged": brain_unchanged,
            "sha256_logic_py": hash_after["logic.py"],
            "sha256_state_py": hash_after["state.py"],
        },
        "roles_status": {
            "CAM": {
                "node_id": cam_source.node_id,
                "health": cam_source.health(),
                "summary": cam_rig.summary(),
            },
            "ESP": {
                "node_id": esp_source.node_id,
                "health": esp_source.health(),
                "telemetry_packets": esp_source.packet_count,
                "packet_loss": esp_source.packet_loss_count,
                "last_telemetry": esp_source.last_telemetry,
                "alerts_sent": esp_commander.total_sent,
                "alerts_acked": esp_commander.total_acked,
            },
            "AI": {
                "adapter_name": hybrid_ai.name,
                "health": hybrid_ai.health(),
                "total_ai_inferences": hybrid_ai.total_inferences,
                "mediapipe_activations": hybrid_ai.mediapipe_activations,
                "mediapipe_activation_ratio_pct": hybrid_ai.mediapipe_activation_ratio,
                "conflicts_rejected": hybrid_ai.conflicts_rejected,
            },
            "RULES": {
                "violations_count": len(violations_detected),
                "rules_triggered": [v.rule for v in violations_detected],
            },
            "RESP": {
                "ring_buffer_health": ring_buffer.health(),
                "packages_dispatched_to_server": len(dispatched_packages),
                "esp_speaker_ack_ready": all(a.i2s_ready for a in esp_alerts_sent),
            },
            "DIAG": {
                "system_state": engine.breaker.system_state,
                "errors_recorded": engine.diagnose.count,
            },
        },
        "performance_60fps_metrics": {
            "scenario_frames": 600,
            "ai_sampled_frames_in_10s": passed_10s,
            "throttled_dropped_frames_in_10s": dropped_10s,
            "avg_step_latency_ms": round(avg_lat, 4),
            "p50_latency_ms": round(p50_lat, 4),
            "p95_latency_ms": round(p95_lat, 4),
            "p99_latency_ms": round(p99_lat, 4),
            "stress_6000_frames_elapsed_s": round(stress_elapsed_s, 4),
            "max_unthrottled_capture_fps": round(max_throughput_fps, 1),
            "ram_current_mb": round(ram_current / (1024 * 1024), 3),
            "ram_peak_mb": round(ram_peak / (1024 * 1024), 3),
            "total_bench_time_s": round(total_elapsed_s, 3),
        },
        "g5_and_raspberry_projections": {
            "unthrottled_60fps_cpu_load_pct_est": round(unthrottled_60fps_load_pct, 1),
            "throttled_8fps_hybrid_cpu_load_pct_est": round(throttled_8fps_two_stage_load_pct, 1),
            "cpu_saved_pct": round(100.0 - throttled_8fps_two_stage_load_pct, 1),
            "recommended_g5_config": {
                "camera_capture_fps": 60,
                "ring_buffer_capacity": 120,
                "ai_throttle_fps": 8,
                "mediapipe_eye_trigger_s": 1.0,
                "mediapipe_yawn_trigger_s": 0.5,
            },
        },
        "timeline_snapshots": role_timeline_logs,
    }

    report_path = EXP_DIR / "benchmark_60fps_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 98)
    print("   BAO CAO THI NGHIEM EXP-004: CAMERA 60 FPS + HYBRID AI (PBL4-HTTM-AI) + FULL ROLES")
    print("=" * 98)
    print(f"[BRAIN] Contracts (logic.py, state.py) Unchanged : {brain_unchanged} (100% NGUYEN VEN)")
    print(
        f"[CAM]   Capture Target: 60 FPS | Frames: {cam_rig.summary()['frames']} "
        f"| Effective: {cam_rig.summary()['effective_fps']} FPS | Sample PGM Saved: {len(cam_rig.saved_files)}"
    )
    print(
        f"[ESP]   Port: {esp_source.uri} @ {esp_source.baudrate} | Telemetry: {esp_source.packet_count} pkts "
        f"| Loss: {esp_source.packet_loss_count} | Alerts Sent/ACK: {esp_commander.total_sent}/{esp_commander.total_acked}"
    )
    print(
        f"[AI]    Adapter: {hybrid_ai.name} | Inferences: {hybrid_ai.total_inferences} "
        f"| MediaPipe On-Demand: {hybrid_ai.mediapipe_activations} ({hybrid_ai.mediapipe_activation_ratio}%) "
        f"| False Alarms Rejected (CONFLICT): {hybrid_ai.conflicts_rejected}"
    )
    print(
        f"[RULES] Violations Triggered: {len(violations_detected)} -> {[v.rule for v in violations_detected]}"
    )
    print(
        f"[RESP]  Server Packages Sent: {len(dispatched_packages)} | I2S Speaker ACK Ready: "
        f"{report['roles_status']['RESP']['esp_speaker_ack_ready']}"
    )
    print(
        f"[PERF]  Max Capture Throughput: {report['performance_60fps_metrics']['max_unthrottled_capture_fps']} FPS "
        f"| Avg Latency: {avg_lat:.4f} ms (P99: {p99_lat:.4f} ms) | Peak RAM: {report['performance_60fps_metrics']['ram_peak_mb']} MB"
    )
    print("-" * 98)
    print("TIMELINE DIEN BIEN TRANG THAI CUA HE THONG:")
    for line in role_timeline_logs:
        print("  " + line)
    print("-" * 98)
    print("TINH TOAN CHO GIAI DOAN G5 / RASPBERRY PI:")
    print(
        f"  - Neu chay AI tren toan bo 60 FPS : Uoc tinh tai CPU = {unthrottled_60fps_load_pct:.1f}% (QUA TAI >100%)"
    )
    print(
        f"  - Khi dung FpsThrottle (8 FPS) + Hybrid Two-Stage : Tai CPU = {throttled_8fps_two_stage_load_pct:.1f}% "
        f"(Tiet kiem {100.0 - throttled_8fps_two_stage_load_pct:.1f}% CPU, video bang chung van dat 60 FPS muot!)"
    )
    print("=" * 98)
    return report


if __name__ == "__main__":
    run_experiment()
