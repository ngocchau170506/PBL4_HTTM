"""Máy chủ web giám sát trực tiếp cho phòng thí nghiệm (workbench/web_viewer, thành phần D).

Chỉ chạy trên PC, lắng nghe tại 127.0.0.1. Cung cấp:
- Giao diện trực quan xem luồng video MJPEG (/video) kèm bounding box.
- API JSON (/api/snapshot, /api/errors) để trang web cập nhật tự động.
- API xuất dữ liệu (/api/export_csv) lưu vào thư mục logs/.

Tuân thủ quy tắc CLAUDE.md:
- Không import ngược vào src/.
- Tách biệt luồng chụp ảnh và luồng máy chủ web.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

from flask import Flask, Response, jsonify, request, send_from_directory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from alert_dispatcher import AlertDispatcher
from esp32_bridge import Esp32Bridge
from landmark_preview import LandmarkPreview, attach_landmark_preview
from live_runner import MODES, LiveRunner
from state_hub import StateHub
from yolo_preview import YoloPreview, attach_yolo_preview

DEFAULT_LOGS_DIR = ROOT / "logs"
STATIC_DIR = HERE / "static"


def _now_ms() -> int:
    return int(time.time() * 1000)


def generate_mjpeg_stream(hub: StateHub, max_wait_s: float = 0.05) -> Generator[bytes, None, None]:
    """Tạo luồng dữ liệu Multipart MJPEG từ StateHub để trình duyệt hiển thị video trực tiếp."""
    last_seq = -1
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    trailer = b"\r\n"

    while True:
        frame_data = hub.get_frame()
        if frame_data is not None:
            jpeg_bytes, _fid, seq = frame_data
            if seq != last_seq:
                last_seq = seq
                yield boundary + jpeg_bytes + trailer
        time.sleep(max_wait_s)


def export_session_csv(hub: StateHub, logs_dir: Path) -> Path:
    """Xuất thông số hiện tại của StateHub ra file CSV trong thư mục logs/."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = _now_ms()
    file_path = logs_dir / f"web_session_{ts}.csv"

    snap = hub.snapshot()
    meta = snap.get("meta", {})
    lat = snap.get("latency_ms", {})
    errs = snap.get("errors", {})
    dets = snap.get("detections", {}).get("items", [])
    esp = meta.get("esp32", {})

    fieldnames = [
        "ts_ms",
        "uptime_s",
        "mode",
        "adapter",
        "system_state",
        "fps",
        "target_fps",
        "frames_total",
        "ai_calls",
        "lat_avg_ms",
        "lat_p95_ms",
        "error_total",
        "detection_count",
        "esp32_co2_ppm",
        "esp32_gps_ready",
        "esp32_speed_kmh",
        "esp32_last_ack",
    ]

    row = {
        "ts_ms": snap.get("ts_ms", ts),
        "uptime_s": snap.get("uptime_s", 0.0),
        "mode": meta.get("mode", "unknown"),
        "adapter": meta.get("adapter", "unknown"),
        "system_state": meta.get("system_state", "UNKNOWN"),
        "fps": snap.get("fps"),
        "target_fps": meta.get("sampler_target_fps"),
        "frames_total": snap.get("frames_total", 0),
        "ai_calls": meta.get("ai_calls", 0),
        "lat_avg_ms": lat.get("avg"),
        "lat_p95_ms": lat.get("p95"),
        "error_total": errs.get("total", 0),
        "detection_count": len(dets),
        "esp32_co2_ppm": esp.get("co2_ppm"),
        "esp32_gps_ready": 1 if esp.get("gps_ready") else 0,
        "esp32_speed_kmh": esp.get("speed_kmh"),
        "esp32_last_ack": meta.get("esp32_last_ack", esp.get("last_ack", "")),
    }

    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)

    return file_path


def create_app(
    hub: StateHub,
    runner: Optional[LiveRunner] = None,
    esp32_bridge: Optional[Esp32Bridge] = None,
    alert_dispatcher: Optional[AlertDispatcher] = None,
    logs_dir: Optional[Path] = None,
) -> Flask:
    """Tạo và cấu hình Flask app cho web_viewer."""
    app = Flask(__name__, static_folder=str(STATIC_DIR))
    target_logs_dir = Path(logs_dir) if logs_dir is not None else DEFAULT_LOGS_DIR

    @app.route("/")
    def index() -> Response:
        return send_from_directory(str(STATIC_DIR), "index.html")

    @app.route("/video")
    def video_feed() -> Response:
        return Response(
            generate_mjpeg_stream(hub),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    @app.route("/api/snapshot")
    def api_snapshot() -> Response:
        return jsonify(hub.snapshot())

    @app.route("/api/errors")
    def api_errors() -> Response:
        snap = hub.snapshot()
        return jsonify(snap.get("errors", {}))

    @app.route("/api/export_csv", methods=["GET", "POST"])
    def api_export_csv() -> Response:
        try:
            saved_path = export_session_csv(hub, target_logs_dir)
            return jsonify({"ok": True, "path": str(saved_path), "rows": 1})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/api/esp32/command", methods=["POST"])
    def api_esp32_command() -> Response:
        if esp32_bridge is None:
            return jsonify({"ok": False, "error": "Cầu nối ESP32 chưa được kích hoạt"}), 400
        payload = request.get_json(silent=True) or {}
        cmd = str(payload.get("command", "")).strip()
        if not cmd:
            return jsonify({"ok": False, "error": "Thiếu tham số 'command'"}), 400
        try:
            res = esp32_bridge.send_command(cmd)
            return jsonify(res)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/api/esp32/status")
    def api_esp32_status() -> Response:
        if esp32_bridge is None:
            return jsonify({"enabled": False, "status": "disabled"})
        return jsonify(esp32_bridge.get_status())

    @app.route("/api/camera/config", methods=["GET", "POST"])
    def api_camera_config() -> Response:
        if runner is None:
            return jsonify({"ok": False, "error": "Runner chưa được khởi tạo"}), 400
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            if payload.get("toggle_rotate"):
                runner.toggle_rotate_180()
            elif "rotate" in payload:
                val_rot = str(payload["rotate"]).strip()
                if val_rot.isdigit():
                    runner.set_rotation(int(val_rot))
            if payload.get("toggle_flip_h"):
                runner.toggle_flip_h()
            elif "flip_h" in payload:
                if bool(payload["flip_h"]) != runner.flip_h:
                    runner.toggle_flip_h()
            if "fps" in payload:
                val_fps = str(payload["fps"]).strip()
                if val_fps.replace(".", "", 1).isdigit():
                    runner.set_fps(float(val_fps))
        return jsonify({
            "ok": True,
            "rotation": runner.rotation,
            "flip_h": runner.flip_h,
            "fps": runner.fps,
        })

    @app.route("/api/auto_alert/status")
    def api_auto_alert_status() -> Response:
        if alert_dispatcher is None:
            return jsonify({"enabled": False, "status": "disabled"})
        return jsonify(alert_dispatcher.get_status())

    @app.route("/api/auto_alert/config", methods=["POST"])
    def api_auto_alert_config() -> Response:
        if alert_dispatcher is None:
            return jsonify({"ok": False, "error": "AlertDispatcher chưa được kích hoạt"}), 400
        payload = request.get_json(silent=True) or {}
        if "enabled" in payload:
            alert_dispatcher.set_enabled(bool(payload["enabled"]))
        return jsonify({"ok": True, "status": alert_dispatcher.get_status()})

    @app.route("/api/health")
    def api_health() -> Response:
        return jsonify({"status": "ok", "app": "web_viewer"})

    return app


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Chạy máy chủ web kiểm tra trực tiếp (web_viewer).")
    parser.add_argument("--source", choices=MODES, default="mock", help="Nguồn video: mock, webcam, rtsp, video")
    parser.add_argument("--detector", choices=["yolo", "mediapipe", "stub"], default="yolo", help="Bộ nhận diện AI: yolo (best.pt), mediapipe (face_landmarker.task), stub")
    parser.add_argument("--index", type=int, default=0, help="Chỉ số camera")
    parser.add_argument("--uri", default=None, help="Địa chỉ RTSP hoặc đường dẫn file video")
    parser.add_argument("--esp32-port", default=None, help="Cổng Serial ESP32 (ví dụ COM3 hoặc mock)")
    parser.add_argument("--esp32-baud", type=int, default=115200, help="Tốc độ baud Serial ESP32")
    parser.add_argument("--fps", type=int, default=None, help="Tốc độ khung hình mong muốn (ví dụ 30, 60)")
    parser.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0, help="Góc xoay camera (độ)")
    parser.add_argument("--host", default="127.0.0.1", help="Địa chỉ IP lắng nghe")
    parser.add_argument("--port", type=int, default=5000, help="Cổng dịch vụ web")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    hub = StateHub()
    active_detector = None
    if args.detector == "yolo":
        preview = YoloPreview()
        active_detector = attach_yolo_preview(hub, preview=preview)
    elif args.detector == "mediapipe":
        landmark_prev = LandmarkPreview()
        active_detector = attach_landmark_preview(hub, preview=landmark_prev)

    bridge: Optional[Esp32Bridge] = None
    if args.esp32_port:
        bridge = Esp32Bridge(port=args.esp32_port, baudrate=args.esp32_baud, hub=hub)
        bridge.start()
        hub.set_meta("esp32_enabled", True)

    dispatcher = AlertDispatcher(bridge=bridge, hub=hub)

    runner = LiveRunner(
        hub,
        mode=args.source,
        uri=args.uri,
        index=args.index,
        detector=active_detector,
        pace=True,
        rotation=args.rotate,
        fps=args.fps,
        alert_dispatcher=dispatcher,
    )

    setup_err = runner.setup()
    if setup_err is not None:
        if bridge is not None:
            bridge.stop()
        print(f"[FAIL] Không thể khởi tạo live_runner: {setup_err}")
        return 1

    runner.start()
    print("=" * 70)
    print(" MÁY CHỦ GIÁM SÁT TRỰC QUAN (WEB VIEWER) ĐÃ KHỞI CHẠY")
    print(f" Truy cập trình duyệt: http://{args.host}:{args.port}")
    print(f" Chế độ nguồn: {args.source} | Bộ nhận diện: {args.detector}")
    if bridge is not None:
        print(f" Cầu nối ESP32: Cổng {args.esp32_port} @ {args.esp32_baud} baud")
    print(" Bấm Ctrl+C trong Terminal để dừng máy chủ an toàn")
    print("=" * 70)

    app = create_app(hub, runner=runner, esp32_bridge=bridge, alert_dispatcher=dispatcher)
    try:
        app.run(host=args.host, port=args.port, threaded=True)
    except KeyboardInterrupt:
        print("\nĐang tắt máy chủ web...")
    finally:
        runner.stop()
        if bridge is not None:
            bridge.stop()
        print("Đã giải phóng camera, ESP32 và tài nguyên.")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
