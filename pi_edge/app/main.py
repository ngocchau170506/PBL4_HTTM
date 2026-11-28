"""Main entrypoint for PI_AI Raspberry Pi 4 Edge Safety Reflex Runtime."""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUDIO_ESP_ROLES = {"AUDIO_MQ135_NODE", "AUDIO_GPS_NODE"}
HUB_ESP_ROLES = {"COMMS_BLE_GPS_HUB", "COMMS_BLE_WIFI_HUB"}

from pi_edge.core.access_control import AuditLogger, EdgeAccessController
from pi_edge.core.config import EdgeConfig, write_runtime_manifest
from pi_edge.core.telemetry import HardwareSampler, MultiTierMetrics, ThermalPolicy

ALERT_TO_VOICE = {
    "DROWSY": "VOICE:1",
    "YAWNING": "VOICE:2",
    "DISTRACTED": "VOICE:3",
    "PHONE": "VOICE:6",
    "SMOKING": "VOICE:7",
}


def _pin_cpu_cores(cores_csv: str) -> None:
    if not hasattr(os, "sched_setaffinity") or not cores_csv:
        return
    try:
        cores = {int(x.strip()) for x in cores_csv.split(",") if x.strip().isdigit()}
        if cores:
            os.sched_setaffinity(0, cores)
    except Exception:
        pass


def _draw_non_overlapping_label(img, text: str, x: int, y: int, color, placed_rects: list) -> None:
    """Draw a compact pill-backed label while vertically shifting away from existing labels."""
    import cv2

    h, w = img.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.46
    thickness = 1
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
    pad = 3
    lx = max(4, min(max(4, w - tw - pad * 2 - 4), int(x)))
    ly = max(34 + th + pad, min(h - baseline - pad - 2, int(y)))

    for _ in range(5):
        rect = (lx - pad, ly - th - pad, lx + tw + pad, ly + baseline + pad)
        hit = False
        for px1, py1, px2, py2 in placed_rects:
            if not (rect[2] < px1 or rect[0] > px2 or rect[3] < py1 or rect[1] > py2):
                hit = True
                break
        if not hit:
            placed_rects.append(rect)
            break
        ly_next = ly - (th + baseline + pad * 2 + 2)
        if ly_next >= 34 + th + pad:
            ly = ly_next
        else:
            ly = min(h - baseline - pad - 2, ly + th + baseline + pad * 2 + 2)

    rx1 = max(0, lx - pad)
    ry1 = max(30, ly - th - pad)
    rx2 = min(w - 1, lx + tw + pad)
    ry2 = min(h - 1, ly + baseline + pad)
    cv2.rectangle(img, (rx1, ry1), (rx2, ry2), (16, 22, 34), -1)
    cv2.rectangle(img, (rx1, ry1), (rx2, ry2), color, 1)
    cv2.putText(img, text, (lx, ly), font, scale, (245, 250, 255), thickness, cv2.LINE_AA)


def _render_live_hud_overlay(frame_bgr, state: dict, overlay: dict):
    """Draw single-layer AI boxes + telemetry banner onto a copy ONLY while /stream has an active viewer."""
    import cv2

    img = frame_bgr
    h, w = img.shape[:2]
    ds = state.get("driver_state") or {}
    pi = state.get("pi") or {}
    gps = state.get("gps") or {}
    cal = ds.get("calibration") or (overlay.get("calibration") if isinstance(overlay, dict) else None)
    d_esc = ds.get("drowsy_escalation") or {}
    mon = ds.get("monitoring") or {}

    raw_dets = list((overlay.get("detections") or []) if isinstance(overlay, dict) else [])
    placed_labels = []

    pose = overlay.get("pose") if isinstance(overlay, dict) else None
    has_pose_face = bool(isinstance(pose, dict) and pose.get("valid") and pose.get("face_box"))
    has_distracted_cls = any(item[0] == "Distracted Head" for item in raw_dets if len(item) >= 3)

    if has_pose_face:
        fx1, fy1, fx2, fy2 = [int(v) for v in pose["face_box"]]
        if mon.get("mode") == "REST_SLEEP_OFF":
            fcolor = (80, 190, 240)
        elif has_distracted_cls or ds.get("alert_type") == "DISTRACTED":
            fcolor = (0, 60, 255)
        else:
            fcolor = (0, 220, 120)
        cv2.rectangle(img, (fx1, fy1), (fx2, fy2), fcolor, 2)
        ftxt = f"Face Y:{pose.get('yaw_deg', 0):.0f} P:{pose.get('pitch_deg', 0):.0f} R:{pose.get('roll_deg', 0):.0f}"
        if has_distracted_cls:
            ftxt += " [DISTRACTED]"
        _draw_non_overlapping_label(img, ftxt, fx1, max(48, fy1 - 6), fcolor, placed_labels)

    for item in raw_dets:
        try:
            lbl, conf, (x1, y1, x2, y2) = item
            # Prevent 2-layer full-face overlap: BlazeFace already draws the unified face rectangle!
            if has_pose_face and lbl in ("Normal", "Distracted Head"):
                continue
            is_risk = lbl in ("Eye_Closed", "Mouth_Open", "Distracted Head", "smoking", "phone", "Smoking", "Phone")
            color = (0, 60, 255) if is_risk else (240, 180, 40)
            cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
            _draw_non_overlapping_label(img, f"{lbl} {conf:.2f}", int(x1), int(y1) - 6, color, placed_labels)
        except Exception:
            continue

    at = str(ds.get("alert_type") or "NORMAL")
    esc_state = str(d_esc.get("state") or "IDLE")
    mon_mode = str(mon.get("mode") or "AUTO")
    bar_color = (20, 25, 195) if (at != "NORMAL" or d_esc.get("alarm_active")) else (
        (65, 55, 20) if mon_mode == "TEMP_PAUSE_30M" else (
            (35, 50, 75) if mon_mode == "REST_SLEEP_OFF" else (18, 28, 42)
        )
    )
    cv2.rectangle(img, (0, 0), (w, 30), bar_color, -1)
    mode_tag = (
        f"PAUSE:{int((mon.get('pause_remaining_s') or 0) // 60)}m"
        if mon_mode == "TEMP_PAUSE_30M"
        else ("SLEEP_OFF" if mon_mode == "REST_SLEEP_OFF" else "AUTO")
    )
    pres_tag = "CO_NGUOI" if mon.get("presence", "DRIVER_PRESENT") == "DRIVER_PRESENT" else "VANG_MAT"
    if esc_state == "CONTINUOUS_RELAPSE":
        esc_tag = " | BAO_DONG_LIEN_TUC!"
    elif esc_state == "ALARM_LATCHED":
        esc_tag = f" | DROWSY:{d_esc.get('count', 3)}/{d_esc.get('limit', 3)} ALARM"
    elif esc_state == "PROBATION":
        esc_tag = f" | PROB:{int(d_esc.get('probation_remaining_s') or 0)}s"
    elif int(d_esc.get("count") or 0) > 0:
        esc_tag = f" | D:{d_esc.get('count')}/{d_esc.get('limit', 3)}"
    else:
        esc_tag = ""
    gps_txt = (
        f"GPS:{gps.get('speed_kmh', 0):.0f}kmh"
        if (gps.get("is_valid") and gps.get("lat") is not None and gps.get("lon") is not None)
        else f"GPS:NO_FIX"
    )
    hud = (
        f"{at} [{mode_tag}|{pres_tag}]{esc_tag} | Cam:{pi.get('camera_fps', 0):.0f} AI:{pi.get('ai_fps', 0):.1f} | "
        f"T:{pi.get('cpu_temp_c', 0) or 0:.0f}C | {gps_txt}"
    )
    cv2.putText(img, hud[:112], (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (245, 250, 255), 1, cv2.LINE_AA)

    if isinstance(cal, dict) and cal.get("state") == "RUNNING":
        prog = max(0.0, float(cal.get("progress_s") or 0.0))
        dur = max(1.0, float(cal.get("duration_s") or 10.0))
        ratio = max(0.0, min(1.0, prog / dur))
        cv2.rectangle(img, (0, 30), (w, 64), (24, 52, 86), -1)
        fill_w = int(round((w - 16) * ratio))
        cv2.rectangle(img, (8, 54), (w - 8, 60), (45, 70, 95), -1)
        if fill_w > 0:
            cv2.rectangle(img, (8, 54), (8 + fill_w, 60), (40, 210, 255), -1)
        banner = f"MEDIAPIPE 478 ACTIVE (YOLO TAM TAT): {prog:.1f}s / {dur:.1f}s [{cal.get('mode') or 'BOOT'}]"
        cv2.putText(img, banner, (10, 49), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (120, 235, 255), 1, cv2.LINE_AA)
    return img


def run(cli_mode: Optional[str] = None, max_ticks: Optional[int] = None, live_stream: bool = False) -> int:
    if live_stream:
        os.environ["ENABLE_LIVE_STREAM"] = "1"
    cfg = EdgeConfig.from_env(cli_mode=cli_mode)
    _pin_cpu_cores(cfg.ai_cpu_cores)
    manifest_path = write_runtime_manifest(cfg)
    print(f"[PI_AI EDGE] Starting run_mode={cfg.run_mode} profile={cfg.env_profile} manifest={manifest_path}")

    # Strict boot RBAC validation (raises RuntimeError in prod if tokens missing!)
    access = EdgeAccessController.from_env(env_profile=cfg.env_profile)
    audit = AuditLogger(cfg.audit_dir)

    from pi_edge.ai.alert_policy import DrowsyEscalationController, MonitoringModeController
    from pi_edge.ai.pipeline import EdgeAiPipeline
    from pi_edge.camera.camera_config import build_rtsp_candidates, configure_opencv_ffmpeg_env, inspect_eth_camera_route_readonly
    from pi_edge.camera.frame_source import CameraFrameSource
    from pi_edge.comms.esp_ble_client import EspBleHubClient, discover_esp_ports_by_role
    from pi_edge.comms.esp_client import EspClient
    from pi_edge.comms.server_sync import ServerSyncClient
    from pi_edge.evidence.burst_collector import BurstCollector
    from pi_edge.evidence.spool_store import UnifiedSpoolStore
    from pi_edge.web.mini_server import MiniWebServer

    hw = HardwareSampler(interval_s=cfg.hardware_poll_interval_s)
    thermal = ThermalPolicy(cfg.yolo_imgsz, cfg.thermal_warm_c, cfg.thermal_hot_c, cfg.thermal_crit_c)
    metrics = MultiTierMetrics()
    spool = UnifiedSpoolStore(cfg.storage_dir, max_storage_mb=cfg.max_spool_mb)
    clip_sample_fps = max(2.0, min(6.0, float(os.environ.get("CLIP_SAMPLE_FPS", "4.0"))))
    clip_pre_s = max(1.0, min(5.0, float(os.environ.get("CLIP_PRE_EVENT_S", "3.0"))))
    clip_post_s = max(2.0, min(10.0, float(os.environ.get("CLIP_POST_EVENT_S", "7.0"))))
    burst = BurstCollector(
        ring_seconds=clip_pre_s + 1.5,
        sample_fps=clip_sample_fps,
        target_size=(640, 360),
        max_burst_frames=max(40, int((clip_pre_s + clip_post_s) * clip_sample_fps) + 4),
        jpeg_quality=cfg.burst_jpeg_quality,
        pre_event_s=clip_pre_s,
        post_event_s=clip_post_s,
    )
    pipeline = EdgeAiPipeline(cfg)
    drowsy_esc = DrowsyEscalationController(
        window_s=cfg.drowsy_window_s,
        escalation_limit=cfg.drowsy_escalation_limit,
    )
    monitoring = MonitoringModeController(
        absence_alert_s=cfg.absence_alert_s,
        pause_duration_s=cfg.pause_duration_min * 60.0,
        auto_resume_speed_kmh=cfg.auto_resume_speed_kmh,
    )

    sync: Optional[ServerSyncClient] = None
    esp: Optional[EspClient] = None
    esp_ble: Optional[EspBleHubClient] = None
    cam: Optional[CameraFrameSource] = None
    web: Optional[MiniWebServer] = None

    stop_flag = {"stop": False}

    def _sig(_s, _f):
        stop_flag["stop"] = True

    import threading as _th
    if _th.current_thread() is _th.main_thread():
        signal.signal(signal.SIGINT, _sig)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _sig)

    try:
        active_alert_ref = {"type": "NORMAL", "last_active_mono": 0.0}

        def _get_merged_sensor_snapshot():
            snap = esp.get_sensor_snapshot() if esp is not None else {
                "co2_ppm": 0.0,
                "raw_adc": 0,
                "gps": {"lat": None, "lon": None, "speed_kmh": 0.0, "satellites": 0, "is_valid": False, "source": "esp32"},
            }
            if esp_ble is not None and esp_ble.connected:
                hub_gps = esp_ble.get_gps_snapshot()
                # LCK-05 v3: GPS lives on ESP32 #2 (prefer hub GPS; fallback to ESP1 only if hub has no fix)
                if hub_gps.get("is_valid") or not (snap.get("gps") or {}).get("is_valid"):
                    snap["gps"] = hub_gps
            return snap

        def _on_mute_pressed(sensor_snap):
            if sync is not None:
                merged = _get_merged_sensor_snapshot()
                sync.queue_alert_dismissed("PHYSICAL_MUTE_BUTTON", active_alert_ref["type"], gps=merged.get("gps"))

        def _on_hw_event(evt_name: str, _raw: str):
            if sync is not None and evt_name in ("AUTO_CO2_WARN", "AUTO_CO2_CRIT", "ESP_BROWNOUT"):
                sensor = _get_merged_sensor_snapshot()
                sync.queue_violation_burst(
                    alert_type=evt_name,
                    verification_mode="SINGLE_MODEL_YOLO",
                    duration_seconds=1.0,
                    jpeg_frames=[],
                    gps=sensor.get("gps"),
                    co2_ppm=float(sensor.get("co2_ppm") or 0.0),
                    behavior_detail="esp32_hw_event",
                )

        def _probe_client_whoami(client, expected_roles, attempts: int = 3) -> bool:
            if not client.connected:
                return False
            for _ in range(attempts):
                if client.device_role in expected_roles:
                    return True
                r_w = client.send_command("WHOAMI", wait_ack=True, timeout_s=0.9)
                if r_w.ok and client.device_role in expected_roles:
                    return True
                # If wrong role replied immediately, stop retrying so swap recovery runs fast!
                if client.device_role != "UNKNOWN" and client.device_role not in expected_roles:
                    return False
                time.sleep(0.75)
            return client.device_role in expected_roles

        esp_port = cfg.esp_port
        esp_baud = cfg.esp_baudrate
        esp_ble_port = cfg.esp_ble_port
        esp_ble_baud = cfg.esp_ble_baudrate
        need_pre_discovery = False
        if cfg.esp_mode.upper() == "SERIAL" and not os.path.exists(esp_port):
            need_pre_discovery = True
        if cfg.esp_ble_mode.upper() == "SERIAL" and (not os.path.exists(esp_ble_port) or esp_port == esp_ble_port):
            need_pre_discovery = True

        def _discover_and_assign():
            nonlocal esp_port, esp_baud, esp_ble_port, esp_ble_baud
            import glob
            cands = sorted(set(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")))
            if not cands:
                return
            roles = discover_esp_ports_by_role(cands)
            found_audio = None
            found_hub = None
            for rname in ("AUDIO_MQ135_NODE", "AUDIO_GPS_NODE"):
                if rname in roles:
                    found_audio = roles[rname]
                    break
            for rname in ("COMMS_BLE_GPS_HUB", "COMMS_BLE_WIFI_HUB"):
                if rname in roles:
                    found_hub = roles[rname]
                    break
            if found_audio:
                esp_port, esp_baud = found_audio
            if found_hub:
                esp_ble_port, esp_ble_baud = found_hub
            # Never allow both clients to open the exact same serial device!
            if esp_port == esp_ble_port:
                if found_audio and not found_hub:
                    esp_ble_port = ""
                elif found_hub and not found_audio:
                    esp_port = ""

        if need_pre_discovery:
            _discover_and_assign()

        esp = EspClient(
            port=esp_port,
            baudrate=esp_baud,
            mode=cfg.esp_mode,
            require_ack=cfg.esp_require_ack,
            on_hw_event=_on_hw_event,
            on_mute_pressed=_on_mute_pressed,
        )
        esp.start()
        audio_role_ok = True if cfg.esp_mode.upper() == "MOCK" else _probe_client_whoami(esp, AUDIO_ESP_ROLES)

        def _beep_calib_start() -> None:
            active_alert_ref["type"] = "NORMAL"
            if esp is not None:
                esp.send_command("STOP", wait_ack=False)
                time.sleep(0.16)
                esp.send_command("ALERT:1:120", wait_ack=False)

        def _get_live_config() -> dict:
            base = pipeline.get_behavior_permissions()
            d_st = drowsy_esc.get_status()
            m_st = monitoring.get_status()
            base.update(
                {
                    "eye_threshold_seconds": pipeline.fusion.channels["eye"].threshold_s,
                    "yawn_threshold_seconds": pipeline.fusion.channels["mouth"].threshold_s,
                    "drowsy_window_s": d_st["window_s"],
                    "drowsy_escalation_limit": d_st["limit"],
                    "absence_alert_s": monitoring.absence_alert_s,
                    "pause_duration_min": round(monitoring.pause_duration_s / 60.0, 1),
                    "auto_resume_speed_kmh": m_st["auto_resume_speed_kmh"],
                }
            )
            return base

        def _apply_live_config(patch: dict) -> dict:
            if not isinstance(patch, dict):
                return _get_live_config()
            pipeline.set_behavior_permissions(patch)
            if "drowsy_window_s" in patch or "drowsy_escalation_limit" in patch:
                drowsy_esc.configure(
                    window_s=float(patch["drowsy_window_s"]) if "drowsy_window_s" in patch else None,
                    escalation_limit=int(patch["drowsy_escalation_limit"]) if "drowsy_escalation_limit" in patch else None,
                )
            if "absence_alert_s" in patch or "pause_duration_min" in patch or "auto_resume_speed_kmh" in patch:
                monitoring.configure(
                    absence_alert_s=float(patch["absence_alert_s"]) if "absence_alert_s" in patch else None,
                    pause_duration_min=float(patch["pause_duration_min"]) if "pause_duration_min" in patch else None,
                    auto_resume_speed_kmh=float(patch["auto_resume_speed_kmh"]) if "auto_resume_speed_kmh" in patch else None,
                )
            return _get_live_config()

        def _handle_dismiss_drowsy_alarm(source: str) -> tuple[bool, str]:
            now_m = time.monotonic()
            ok_d, detail_d, esp_cmd = drowsy_esc.dismiss_alarm(now_m, source=source)
            if esp_cmd and esp is not None:
                esp.send_command(esp_cmd, wait_ack=False)
            if esp_ble is not None and esp_ble.connected:
                esp_ble.send_notify(f"INFO|Da tat loa canh bao - Pi kiem tra lai trong {int(drowsy_esc.probation_s // 60)} phut")
            if sync is not None:
                sensor = _get_merged_sensor_snapshot()
                sync.queue_alert_dismissed(source, "DROWSY_LEVEL3_DISMISSED", gps=sensor.get("gps"))
            return ok_d, detail_d

        def _handle_set_monitoring_mode(mode_name: str, source: str) -> tuple[bool, str]:
            now_m = time.monotonic()
            ok_m, detail_m = monitoring.set_mode(mode_name, now_mono=now_m, source=source)
            if ok_m and mode_name == "REST_SLEEP_OFF":
                drowsy_esc.reset()
                pipeline.fusion.reset_all()
                active_alert_ref["type"] = "NORMAL"
                if esp is not None:
                    esp.send_command("ALARM:OFF", wait_ack=False)
            if ok_m and esp_ble is not None and esp_ble.connected:
                esp_ble.send_notify(f"INFO|Che do giam sat: {mode_name}")
            if ok_m and sync is not None:
                sensor = _get_merged_sensor_snapshot()
                sync.queue_alert_dismissed(source, f"MODE_{mode_name}", gps=sensor.get("gps"))
            return ok_m, detail_m

        def _exec_cmd(cmd: str):
            norm_cmd = (cmd or "").strip().upper()
            if norm_cmd == "CALIBRATE":
                cam_ready = (not cfg.disable_camera) and (cam is not None) and cam.is_opened()
                if not cam_ready:
                    if esp_ble is not None and esp_ble.connected:
                        esp_ble.send_notify("CALIB_FAIL|NO_CAMERA")
                    return False, "NO_CAMERA"
                ok_cal, cal_detail = pipeline.request_calibration(source="CONTROL_CMD", lock=True)
                if ok_cal:
                    _beep_calib_start()
                    if esp_ble is not None and esp_ble.connected:
                        esp_ble.send_notify("CALIB_START|10s")
                    return True, "CALIBRATION_STARTED"
                return False, cal_detail

            if norm_cmd == "DISMISS_DROWSY_ALARM":
                return _handle_dismiss_drowsy_alarm("REMOTE_OPERATOR")

            if norm_cmd == "MODE_AUTO":
                return _handle_set_monitoring_mode("AUTO", "REMOTE_OPERATOR")

            if norm_cmd == "MODE_PAUSE_30M":
                return _handle_set_monitoring_mode("TEMP_PAUSE_30M", "REMOTE_OPERATOR")

            if norm_cmd == "MODE_REST_OFF":
                return _handle_set_monitoring_mode("REST_SLEEP_OFF", "REMOTE_OPERATOR")

            if norm_cmd == "REST_BREAK_REQUEST":
                active_alert_ref["type"] = "NORMAL"
                if esp is not None:
                    esp.send_command("MUTE", wait_ack=False)
                if sync is not None:
                    sensor = _get_merged_sensor_snapshot()
                    sync.queue_alert_dismissed("REMOTE_OPERATOR", "REST_BREAK_START", gps=sensor.get("gps"))
                return True, "REST_BREAK_ACK"

            # If a latched Level-3 drowsy alarm is active and MUTE/STOP arrives, transition cleanly into probation
            if norm_cmd in ("MUTE", "STOP", "ALARM:OFF") and drowsy_esc.get_status().get("alarm_active"):
                _handle_dismiss_drowsy_alarm("REMOTE_OPERATOR")
                if norm_cmd == "MUTE":
                    res = esp.send_command("MUTE")
                    return res.ok, (res.ack_line or res.error)
                return True, "ALARM_OFF_PROBATION_STARTED"

            res = esp.send_command(cmd)
            if res.ok and norm_cmd == "MUTE" and sync is not None:
                sensor = _get_merged_sensor_snapshot()
                sync.queue_alert_dismissed("REMOTE_OPERATOR", active_alert_ref["type"], gps=sensor.get("gps"))
            return res.ok, (res.ack_line or res.error)

        auth_tok = (os.environ.get("REMOTE_AUTH_TOKEN") or os.environ.get("DEVICE_BRIDGE_TOKEN") or "").strip()
        sync = ServerSyncClient(
            cfg=cfg,
            auth_token=auth_tok,
            spool=spool,
            on_command=_exec_cmd,
            on_remote_config=_apply_live_config,
        )
        sync.start()

        def _on_ble_action(action: str):
            sensor = _get_merged_sensor_snapshot()
            if action == "DISMISS_DROWSY_ALARM":
                _handle_dismiss_drowsy_alarm("PHONE_BLE")
            elif action == "MODE_AUTO":
                _handle_set_monitoring_mode("AUTO", "PHONE_BLE")
            elif action == "MODE_PAUSE_30M":
                _handle_set_monitoring_mode("TEMP_PAUSE_30M", "PHONE_BLE")
            elif action == "MODE_REST_OFF":
                _handle_set_monitoring_mode("REST_SLEEP_OFF", "PHONE_BLE")
            elif action == "MUTE_ALERT":
                if drowsy_esc.get_status().get("alarm_active"):
                    _handle_dismiss_drowsy_alarm("PHONE_BLE")
                if esp is not None:
                    esp.send_command("MUTE", wait_ack=False)
                if sync is not None:
                    sync.queue_alert_dismissed("PHONE_BLE", active_alert_ref["type"], gps=sensor.get("gps"))
            elif action in ("CALIBRATE", "CALIBRATE_LOCK"):
                cam_ready = (not cfg.disable_camera) and (cam is not None) and cam.is_opened()
                if not cam_ready:
                    if esp_ble is not None and esp_ble.connected:
                        esp_ble.send_notify("CALIB_FAIL|NO_CAMERA")
                    return
                ok_cal, _ = pipeline.request_calibration(source="APP_BLE", lock=True)
                if ok_cal:
                    _beep_calib_start()
                    if esp_ble is not None and esp_ble.connected:
                        esp_ble.send_notify("CALIB_START|10s")
            elif action == "SOS" and sync is not None:
                sync.queue_violation_burst(
                    alert_type="SOS",
                    verification_mode="SINGLE_MODEL_YOLO",
                    duration_seconds=1.0,
                    jpeg_frames=[],
                    gps=sensor.get("gps"),
                    co2_ppm=float(sensor.get("co2_ppm") or 0.0),
                    behavior_detail="driver_ble_sos_button",
                )
            elif action in ("ACK_AWAKE", "REST_BREAK_START", "REST_BREAK_END") and sync is not None:
                sync.queue_alert_dismissed("PHONE_BLE", action, gps=sensor.get("gps"))

        esp_ble = EspBleHubClient(
            port=esp_ble_port,
            baudrate=esp_ble_baud,
            mode=cfg.esp_ble_mode,
            on_ble_action=_on_ble_action,
        )
        esp_ble_started = esp_ble.start()
        hub_role_ok = True if cfg.esp_ble_mode.upper() in ("DISABLED", "MOCK") else (
            esp_ble_started and _probe_client_whoami(esp_ble, HUB_ESP_ROLES)
        )

        # Automatic role-swap / baud-mismatch recovery (fixes BUG-01 when /dev/ttyUSB0 <-> ttyUSB1 swap!)
        if (cfg.esp_mode.upper() == "SERIAL" and not audio_role_ok) or (
            cfg.esp_ble_mode.upper() == "SERIAL" and not hub_role_ok
        ):
            print(
                f"[PI_AI ESP] Role/port mismatch detected (esp1={esp.port}:{esp.device_role}, "
                f"esp2={esp_ble.port}:{esp_ble.device_role}) -> running WHOAMI auto-discovery...",
                file=sys.stderr,
            )
            esp.close()
            esp_ble.close()
            orig_esp_port, orig_ble_port = cfg.esp_port, cfg.esp_ble_port
            _discover_and_assign()
            # Never leave either client permanently dead/closed if one board was temporarily unplugged (F-019)
            if not esp_port and orig_esp_port and orig_esp_port != esp_ble_port:
                esp_port = orig_esp_port
            if not esp_ble_port and orig_ble_port and orig_ble_port != esp_port:
                esp_ble_port = orig_ble_port
            if esp_port:
                esp = EspClient(
                    port=esp_port,
                    baudrate=esp_baud,
                    mode=cfg.esp_mode,
                    require_ack=cfg.esp_require_ack,
                    on_hw_event=_on_hw_event,
                    on_mute_pressed=_on_mute_pressed,
                )
                esp.start()
                audio_role_ok = _probe_client_whoami(esp, AUDIO_ESP_ROLES, attempts=2)
            if cfg.esp_ble_mode.upper() == "SERIAL" and esp_ble_port:
                esp_ble = EspBleHubClient(
                    port=esp_ble_port,
                    baudrate=esp_ble_baud,
                    mode=cfg.esp_ble_mode,
                    on_ble_action=_on_ble_action,
                )
                esp_ble.start()
                hub_role_ok = _probe_client_whoami(esp_ble, HUB_ESP_ROLES, attempts=2)
            print(
                f"[PI_AI ESP] Auto-discovery result: esp1={esp.port}@{esp.baudrate}({esp.device_role}) "
                f"esp2={esp_ble.port}@{esp_ble.baudrate}({esp_ble.device_role})",
                file=sys.stderr,
            )

        # NEVER push CFG_TOKEN / CFG_SERVER / CFG_WIFI unless ESP32 #2 role is verified as Hub!
        if cfg.esp_ble_mode.upper() == "SERIAL" and esp_ble.connected and hub_role_ok and cfg.run_mode == "server":
            surv_ssid = os.environ.get("ESP_SURVIVAL_WIFI_SSID", "").strip()
            surv_pass = os.environ.get("ESP_SURVIVAL_WIFI_PASS", "")
            esp_ble.sync_runtime_config(cfg.vehicle_id, cfg.server_url, auth_tok, wifi_ssid=surv_ssid, wifi_pass=surv_pass)

        web = MiniWebServer(
            host=cfg.web_host,
            port=cfg.web_port,
            web_mode=cfg.web_mode,
            access=access,
            audit=audit,
            violations_dir=cfg.storage_dir,
            on_control_cmd=_exec_cmd,
            on_stream_frame_sent=lambda: metrics.mark("stream"),
            on_config_get=_get_live_config,
            on_config_update=_apply_live_config,
            overlay_fn=lambda f, st: _render_live_hud_overlay(f, st, pipeline.get_latest_overlay()),
            max_stream_fps=min(cfg.live_stream_max_fps, float(cfg.camera_fps)),
            jpeg_quality=cfg.live_stream_jpeg_quality,
            enable_live_stream=cfg.enable_live_stream,
            app_iot_dir=ROOT / "APP_IOT",
        )
        try:
            actual_port = web.start()
            if cfg.enable_live_stream:
                print(
                    f"[PI_AI LIVE CAM] Open in browser: http://{sync.local_ip}:{actual_port}/ "
                    f"(dang nhap bang WEB_VIEWER_TOKEN hoac /stream?token=<WEB_VIEWER_TOKEN>)",
                    flush=True,
                )
            if cfg.enable_live_stream or cfg.web_mode == "full_local":
                print(
                    f"[PI_AI DRIVER APP] Driver App: http://{sync.local_ip}:{actual_port}/driver",
                    flush=True,
                )
        except OSError as exc:
            if cfg.run_mode == "server":
                print(
                    f"[PI_AI WEB WARN] Port {cfg.web_port} unavailable ({exc}); continuing safety audio & server sync without local web.",
                    file=sys.stderr,
                )
                web = None
            else:
                raise

        hw.start()
        pipeline.start()

        if not cfg.disable_camera:
            import cv2
            configure_opencv_ffmpeg_env(ffmpeg_threads=1)
            route_info = inspect_eth_camera_route_readonly(cfg.camera_ip)
            print(f"[PI_AI CAMERA] Read-only eth0 route check: {route_info}")
            cands = build_rtsp_candidates(
                cfg.camera_uri,
                cfg.camera_ip,
                os.environ.get("FD_CAMERA_PASSWORD", "").strip(),
                prefer_substream=cfg.camera_prefer_substream,
            )

            def _open_cam():
                for uri in cands:
                    c = cv2.VideoCapture(uri, cv2.CAP_FFMPEG)
                    if c.isOpened():
                        return c
                    c.release()
                c = cv2.VideoCapture(0)
                if c.isOpened():
                    return c
                c.release()
                return None

            cam = CameraFrameSource(
                open_fn=_open_cam,
                target_size=(cfg.camera_width, cfg.camera_height),
                rotation_deg=cfg.camera_rotation,
                stall_reset_s=cfg.camera_stall_reset_s,
            )
            cam.start()

        last_frame_id = 0
        last_infer_ts = 0.0
        last_voice_sent_mono = -999.0
        last_behavior_voice_by_type: dict = {}
        voice_cooldown_s = max(2.0, float(os.environ.get("ESP_VOICE_COOLDOWN_S", "5.0")))
        behavior_voice_cooldown_s = max(6.0, float(os.environ.get("ESP_BEHAVIOR_VOICE_COOLDOWN_S", "10.0")))
        burst_cooldown_s = max(10.0, float(os.environ.get("VIOLATION_BURST_COOLDOWN_S", "20.0")))
        last_burst_started_by_type: dict = {}
        interrupt_voice = os.environ.get("ESP_INTERRUPT_VOICE", "0").strip().lower() in ("1", "true", "yes")
        last_alert_type = "NORMAL"
        last_cal_progress_notify_mono = -999.0
        last_cal_log_state = "IDLE"
        last_cal_log_mono = -999.0
        applied_native_stream_fps: Optional[float] = None
        tick_count = 0
        target_dt = 1.0 / max(1.0, cfg.camera_fps)

        while not stop_flag["stop"]:
            t_loop = time.monotonic()
            metrics.mark("loop")
            hw_snap = hw.snapshot()
            plan = thermal.plan(hw_snap.get("temp_c"), int(hw_snap.get("throttled_bits") or 0))
            esp_sensor_pre = _get_merged_sensor_snapshot()
            esp_health_pre = (getattr(esp, "latest_health", None) or {}) if esp is not None else {}

            pkt = cam.read_latest(last_seen_id=last_frame_id, timeout_s=0.05) if cam else None
            if pkt is not None and not pkt.is_stale:
                last_frame_id = pkt.frame_id
                metrics.mark("camera")
                burst.push_clean_frame(pkt.frame_id, pkt.image, now_mono=t_loop)
                if web is not None and cam is not None and cam.native_fps and cam.native_fps != applied_native_stream_fps:
                    applied_native_stream_fps = cam.native_fps
                    web.set_max_stream_fps(min(cfg.live_stream_max_fps, cam.native_fps))

            if pkt is not None:
                ai_state = pipeline.evaluate_tick(
                    pkt.image,
                    plan,
                    now_mono=t_loop,
                    is_stale=bool(pkt.is_stale),
                    co2_ppm=float(esp_sensor_pre.get("co2_ppm") or 0.0),
                    mq135_warmup=bool(esp_health_pre.get("warmup", False)),
                )
                inf_ts = float(ai_state.get("infer_ts_mono") or 0.0)
                if not pkt.is_stale and inf_ts > last_infer_ts and ai_state["ai_latency_ms"] > 0:
                    last_infer_ts = inf_ts
                    metrics.mark("ai")
            else:
                ai_state = {
                    "alert_state": "NORMAL",
                    "alert_type": "NORMAL",
                    "verification_mode": "SINGLE_MODEL_YOLO",
                    "alert_reason": "NO_CAMERA",
                    "duration_seconds": 0.0,
                    "face_detected": False,
                    "detected_classes": [],
                    "behavior_detail": "",
                    "ai_latency_ms": 0.0,
                    "allow_smoking": cfg.allow_smoking,
                    "allow_phone": cfg.allow_phone,
                    "calibration": pipeline.get_calibration_status(now_mono=t_loop),
                }
            ai_state["thermal_level"] = plan.thermal_level

            # 3-Mode Presence & Cabin Monitoring Policy (AUTO / TEMP_PAUSE_30M / REST_SLEEP_OFF)
            gps_pre = esp_sensor_pre.get("gps") or {}
            cam_online = (pkt is not None) and (not bool(pkt.is_stale)) and (ai_state.get("alert_reason") != "NO_CAMERA")
            mon_res = monitoring.update(
                face_detected=bool(ai_state.get("face_detected", False)),
                camera_online=cam_online,
                gps_valid=bool(gps_pre.get("is_valid", False)),
                speed_kmh=float(gps_pre.get("speed_kmh") or 0.0),
                now_mono=t_loop,
            )
            if mon_res.event == "DRIVER_ABSENT":
                if esp_ble is not None and esp_ble.connected:
                    esp_ble.send_notify(f"DRIVER_ABSENT|{mon_res.event_detail}")
                if sync is not None:
                    sync.queue_violation_burst(
                        alert_type="DRIVER_ABSENT",
                        verification_mode=ai_state["verification_mode"],
                        duration_seconds=monitoring.absence_alert_s,
                        jpeg_frames=[],
                        gps=gps_pre,
                        co2_ppm=float(esp_sensor_pre.get("co2_ppm") or 0.0),
                        behavior_detail=mon_res.event_detail,
                    )
            elif mon_res.event == "MONITORING_AUTO_RESUMED_BY_SPEED":
                if esp is not None:
                    esp.send_command("ALERT:1:160", wait_ack=False)
                if esp_ble is not None and esp_ble.connected:
                    esp_ble.send_notify(f"MODE_AUTO|{mon_res.event_detail}")
                if sync is not None:
                    sync.queue_violation_burst(
                        alert_type="MONITORING_AUTO_RESUMED_BY_SPEED",
                        verification_mode=ai_state["verification_mode"],
                        duration_seconds=3.0,
                        jpeg_frames=[],
                        gps=gps_pre,
                        co2_ppm=float(esp_sensor_pre.get("co2_ppm") or 0.0),
                        behavior_detail=mon_res.event_detail,
                    )
            elif mon_res.event == "PAUSE_EXPIRED_AUTO":
                if esp_ble is not None and esp_ble.connected:
                    esp_ble.send_notify(f"INFO|{mon_res.event_detail}")

            # Drowsiness 3-Strike Escalation & Probation Policy
            is_drowsy_triggered = (
                (not mon_res.alerts_suppressed)
                and ai_state["alert_state"] == "EDGE_TRIGGERED"
                and ai_state["alert_type"] == "DROWSY"
            )
            drowsy_act = drowsy_esc.update(is_drowsy_triggered, now_mono=t_loop, suppressed=mon_res.alerts_suppressed)
            if mon_res.alerts_suppressed:
                ai_state["alert_state"] = "NORMAL"
                ai_state["alert_type"] = "NORMAL"
                ai_state["alert_reason"] = "REST_SLEEP_OFF"
            ai_state["drowsy_escalation"] = drowsy_esc.get_status(now_mono=t_loop)
            ai_state["monitoring"] = monitoring.get_status(now_mono=t_loop)

            if drowsy_act.esp_cmd and esp is not None:
                t_a = time.perf_counter()
                esp.send_command(drowsy_act.esp_cmd, wait_ack=False)
                metrics.record_alert_latency_ms((time.perf_counter() - t_a) * 1000.0)
                last_voice_sent_mono = t_loop
            if drowsy_act.ble_notify and esp_ble is not None and esp_ble.connected:
                esp_ble.send_notify(drowsy_act.ble_notify)
            if drowsy_act.server_incident and sync is not None:
                sync.queue_violation_burst(
                    alert_type=drowsy_act.server_incident,
                    verification_mode=ai_state["verification_mode"],
                    duration_seconds=float(ai_state.get("duration_seconds") or 2.0),
                    jpeg_frames=[],
                    gps=gps_pre,
                    co2_ppm=float(esp_sensor_pre.get("co2_ppm") or 0.0),
                    behavior_detail=drowsy_act.ble_notify or drowsy_act.server_incident,
                )

            cal_cur = ai_state.get("calibration")
            if isinstance(cal_cur, dict):
                c_st = str(cal_cur.get("state") or "IDLE")
                if c_st == "RUNNING":
                    if last_cal_log_state != "RUNNING":
                        print(
                            f"[PI_AI MEDIAPIPE] BAT DAU CAN CHINH {cal_cur.get('duration_s', 10.0):.0f}s "
                            f"(mode={cal_cur.get('mode') or 'BOOT'}) — TAM TAT YOLO",
                            flush=True,
                        )
                        last_cal_log_mono = t_loop
                    elif (t_loop - last_cal_log_mono) >= 2.0:
                        print(
                            f"[PI_AI MEDIAPIPE] Dang chay 478 diem: {cal_cur.get('progress_s', 0.0):.1f}s / "
                            f"{cal_cur.get('duration_s', 10.0):.0f}s (face={ai_state.get('face_detected')})",
                            flush=True,
                        )
                        last_cal_log_mono = t_loop
                last_cal_log_state = c_st
            if (
                isinstance(cal_cur, dict)
                and cal_cur.get("state") == "RUNNING"
                and cal_cur.get("mode") == "APP_LOCK"
                and esp_ble is not None
                and esp_ble.connected
                and (t_loop - last_cal_progress_notify_mono) >= 2.0
            ):
                last_cal_progress_notify_mono = t_loop
                esp_ble.send_notify(f"CALIB_PROGRESS|{int(cal_cur.get('progress_s') or 0)}s")

            while True:
                cal_evt = pipeline.pop_calibration_event()
                if cal_evt is None:
                    break
                if cal_evt.get("state") == "DONE":
                    tag = "locked" if cal_evt.get("locked") else "temp"
                    print(
                        f"[PI_AI MEDIAPIPE] HOAN TAT 10s (mode={cal_evt.get('mode')}, {tag}, "
                        f"ear_thresh={cal_evt.get('ear_closed_threshold')}, glare={cal_evt.get('glare_mode')}) "
                        f"-> TAT MEDIAPIPE, BAT LAI YOLO!",
                        flush=True,
                    )
                    if esp_ble is not None and esp_ble.connected:
                        esp_ble.send_notify(f"CALIB_DONE|{tag}")
                    if esp is not None:
                        esp.send_command("ALERT:1:180", wait_ack=False)
                elif cal_evt.get("state") == "FAILED":
                    reason_fail = str(cal_evt.get("reason") or "NO_FACE")
                    print(
                        f"[PI_AI MEDIAPIPE] KET THUC 10s CHUA DU MAU (reason={reason_fail}) "
                        f"-> TAT MEDIAPIPE, CHUYEN SANG YOLO!",
                        flush=True,
                    )
                    if esp_ble is not None and esp_ble.connected:
                        esp_ble.send_notify(f"CALIB_FAIL|{reason_fail}")
                    if esp is not None:
                        esp.send_command("ALERT:1:180", wait_ack=False)

            cur_alert = ai_state["alert_type"]
            if cur_alert and cur_alert != "NORMAL":
                active_alert_ref["type"] = cur_alert
                active_alert_ref["last_active_mono"] = t_loop
            elif active_alert_ref["type"] != "NORMAL" and (t_loop - active_alert_ref["last_active_mono"]) > 10.0:
                active_alert_ref["type"] = "NORMAL"

            alarm_latched_now = bool((ai_state.get("drowsy_escalation") or {}).get("alarm_active"))
            if ai_state["alert_state"] == "EDGE_TRIGGERED" and not mon_res.alerts_suppressed:
                # Tier-1 immediate offline-capable ESP32 speaker reflex (<5ms).
                # When Level-3/Relapse continuous alarm is latched, keep ESP32 #1 on the emergency alarm loop.
                if not alarm_latched_now:
                    if cur_alert == "DROWSY":
                        # Strike 1-2 initial voice already sent via drowsy_act; repeat if driver stays asleep past cooldown
                        if not drowsy_act.esp_cmd and (t_loop - last_voice_sent_mono) >= voice_cooldown_s:
                            t_a = time.perf_counter()
                            esp.send_command("VOICE:1", wait_ack=False)
                            metrics.record_alert_latency_ms((time.perf_counter() - t_a) * 1000.0)
                            last_voice_sent_mono = t_loop
                    elif cur_alert in ("SMOKING", "PHONE"):
                        last_beh = float(last_behavior_voice_by_type.get(cur_alert, -999.0))
                        if (t_loop - last_beh) >= behavior_voice_cooldown_s and (t_loop - last_voice_sent_mono) >= voice_cooldown_s:
                            voice_cmd = ALERT_TO_VOICE.get(cur_alert, "VOICE:6")
                            t_a = time.perf_counter()
                            esp.send_command(voice_cmd, wait_ack=False)
                            metrics.record_alert_latency_ms((time.perf_counter() - t_a) * 1000.0)
                            if esp_ble is not None and esp_ble.connected:
                                esp_ble.send_notify(f"{cur_alert}|{ai_state['alert_reason']}")
                            last_voice_sent_mono = t_loop
                            last_behavior_voice_by_type[cur_alert] = t_loop
                    else:
                        if (t_loop - last_voice_sent_mono) >= voice_cooldown_s:
                            voice_cmd = ALERT_TO_VOICE.get(cur_alert, "VOICE:3")
                            t_a = time.perf_counter()
                            esp.send_command(voice_cmd, wait_ack=False)
                            metrics.record_alert_latency_ms((time.perf_counter() - t_a) * 1000.0)
                            if esp_ble is not None and esp_ble.connected:
                                esp_ble.send_notify(f"{cur_alert}|{ai_state['duration_seconds']:.1f}s")
                            last_voice_sent_mono = t_loop
                # Start one 10-second continuous evidence window (-3s pre -> +7s post) with per-type cooldown (blocks spam storms!)
                last_bt = float(last_burst_started_by_type.get(cur_alert, -999.0))
                if not burst.has_active_window and (t_loop - last_bt) >= burst_cooldown_s:
                    sensor = _get_merged_sensor_snapshot()
                    started = burst.start_window(
                        meta={
                            "alert_type": cur_alert,
                            "verification_mode": ai_state["verification_mode"],
                            "duration_seconds": float(ai_state["duration_seconds"]),
                            "confidence": float(ai_state.get("confidence") or 0.0),
                            "gps": sensor.get("gps"),
                            "co2_ppm": float(sensor.get("co2_ppm") or 0.0),
                            "behavior_detail": ai_state["behavior_detail"] or ai_state.get("alert_reason", ""),
                        },
                        now_mono=t_loop,
                        fallback_frame=pkt.image if pkt else None,
                    )
                    if started:
                        last_burst_started_by_type[cur_alert] = t_loop
            elif interrupt_voice and not alarm_latched_now and cur_alert == "NORMAL" and last_alert_type != "NORMAL":
                # Only interrupt voice mid-sentence if explicitly enabled via ESP_INTERRUPT_VOICE=1 (default=0)
                esp.send_command("STOP", wait_ack=False)
            last_alert_type = cur_alert

            completed = burst.poll_completed_window(now_mono=t_loop)
            if completed is not None:
                w_meta, jpegs, eff_fps = completed
                sync.queue_violation_burst(
                    alert_type=w_meta["alert_type"],
                    verification_mode=w_meta["verification_mode"],
                    duration_seconds=w_meta["duration_seconds"],
                    jpeg_frames=jpegs,
                    gps=w_meta.get("gps"),
                    co2_ppm=float(w_meta.get("co2_ppm") or 0.0),
                    behavior_detail=w_meta.get("behavior_detail") or "",
                    burst_fps=eff_fps,
                    confidence=float(w_meta.get("confidence") or 0.0) or None,
                )

            fps_snap = metrics.snapshot()
            esp_sensor_snap = _get_merged_sensor_snapshot()
            if esp_ble is not None and esp_ble.connected:
                esp_ble.send_heartbeat_if_due(esp_sensor_snap, now_mono=t_loop, interval_s=1.5)
                esp_ble.request_status_if_due(now_mono=t_loop, interval_s=10.0)
            payload = sync.build_telemetry_payload(
                ai_state=ai_state,
                hw_snap=hw_snap,
                fps_snap=fps_snap,
                esp_sensor=esp_sensor_snap,
                esp_status=esp.get_esp_status_block(),
                mute_state=esp.pop_mute_state_block(),
                camera_stale=bool(pkt.is_stale) if pkt else True,
                camera_error=cam.error if cam else "camera_disabled",
                bluetooth_phone=esp_ble.get_bluetooth_block() if esp_ble else None,
            )
            # Single update_state() per tick (fixes 3x/frame contention!)
            sync.update_state(payload)
            if web is not None:
                web.update_frame(
                    pkt.frame_id if pkt else 0,
                    pkt.image if pkt else None,
                    payload,
                    is_stale=bool(pkt.is_stale) if pkt else True,
                )

            tick_count += 1
            if max_ticks is not None and tick_count >= max_ticks:
                break
            sleep_s = target_dt - (time.monotonic() - t_loop)
            if sleep_s > 0.001:
                time.sleep(sleep_s)
    finally:
        if sync is not None:
            flushed = burst.poll_completed_window(force_flush=True)
            if flushed is not None:
                w_meta, jpegs, eff_fps = flushed
                try:
                    sync.queue_violation_burst(
                        alert_type=w_meta["alert_type"],
                        verification_mode=w_meta["verification_mode"],
                        duration_seconds=w_meta["duration_seconds"],
                        jpeg_frames=jpegs,
                        gps=w_meta.get("gps"),
                        co2_ppm=float(w_meta.get("co2_ppm") or 0.0),
                        behavior_detail=w_meta.get("behavior_detail") or "",
                        burst_fps=eff_fps,
                    )
                except Exception:
                    pass
        for obj in (cam, pipeline, web, sync, esp, esp_ble, hw, spool):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="PI_AI Raspberry Pi 4 Edge Safety Reflex")
    ap.add_argument("--mode", choices=["local", "server"], default=None, help="Override RUN_MODE")
    ap.add_argument(
        "--live-stream",
        action="store_true",
        help="Enable Pi browser live camera viewer (http://<pi-ip>:8080/) with on-demand AI HUD overlay",
    )
    args = ap.parse_args()
    return run(cli_mode=args.mode, live_stream=bool(args.live_stream))


if __name__ == "__main__":
    raise SystemExit(main())
