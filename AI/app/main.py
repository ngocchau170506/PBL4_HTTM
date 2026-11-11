"""ĐIỂM KHỞI CHẠY CHÍNH TRÊN XE (PRODUCTION ENTRYPOINT — PROPOSE-CR-001).

- Dịch vụ `driver-monitor.service` khởi chạy trực tiếp `python -m AI.app.main`.
- Vòng lặp thời gian thực điều phối trực tiếp các luồng trong `AI/scripts/*`
  (CameraFrameSource, DrowsinessPipeline, EspCommunicator, CloudSyncManager, EmbeddedFpsGovernor).
- Sử dụng kiểu dữ liệu chẩn đoán chuẩn (`ErrorCode`, `ErrorEvent`) rút từ `src/contracts/logic.py`
  qua `AI/scripts/diagnostic_log.py`. KHÔNG đi qua khung mô phỏng `src/fd_flow/FlowEngine`.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

repo_root = Path(__file__).resolve().parents[2]
for p in (str(repo_root), str(repo_root / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _autoload_pi_env() -> None:
    """Tự động nạp biến môi trường từ AI/deploy/pi.env trên Linux (Raspberry Pi) hoặc khi LOAD_PI_ENV=1,
    dùng setdefault để không ghi đè các biến người dùng đã chủ động export qua SSH."""
    if not (sys.platform.startswith("linux") or os.getenv("LOAD_PI_ENV", "0") == "1"):
        return
    env_path = repo_root / "AI" / "deploy" / "pi.env"
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            # Cắt bỏ comment cuối dòng nếu không nằm trong dấu nháy
            if " #" in val and not (val.startswith(("'", '"')) and val.endswith(("'", '"'))):
                val = val.split(" #", 1)[0]
            val = val.strip().strip("'").strip('"')
            if key:
                os.environ.setdefault(key, val)
    except Exception:
        pass


_autoload_pi_env()

from AI.scripts.telemetry import EmbeddedFpsGovernor, configure_embedded_threads

# Khởi tạo giới hạn luồng tính toán phần cứng trước khi nạp OpenCV / PyTorch
configure_embedded_threads(max_threads=2)

import cv2

try:
    from AI.app.dashboard import Dashboard
except ImportError:
    Dashboard = None

from AI.app.web_server import DriverMonitorWebServer

from AI.scripts.calibration import (
    BASELINE_PATH,
    CALIBRATION_SECONDS,
    MODEL_PATH,
    CalibrationSession,
    arm64_lacks_aes,
    calculate_head_pose,
    create_face_landmarker,
    make_mp_image,
)
from AI.scripts.camera_config import open_camera, wait_for_external_camera
from AI.scripts.camera_frame_source import CameraFrameSource
from AI.scripts.cloud_sync import CloudSyncManager
from AI.scripts.drowsiness_pipeline import DrowsinessPipeline
from AI.scripts.esp_communication import EspCommunicator
from AI.scripts.event_recorder import EventRecorder

_arm64_lacks_aes = arm64_lacks_aes

WINDOW_TITLE = "Assess the driver's condition"
STABLE_FRAMES_REQUIRED = 15


def _detect_alignment(frame, landmarker, timestamp):
    height, width = frame.shape[:2]
    image = make_mp_image(frame, landmarker)
    result = landmarker.detect_for_video(image, timestamp)
    if not result.face_landmarks:
        return False, {"FACE": "KHONG THAY"}
    pose = calculate_head_pose(result.face_landmarks[0], width, height)
    if pose is None:
        return False, {"FACE": "THAY", "POSE": "KHONG TIN CAY"}
    yaw, pitch, roll = pose
    stable = abs(yaw) <= 15 and abs(pitch) <= 15 and abs(roll) <= 10
    return stable, {
        "FACE": "OK",
        "YAW": f"{yaw:.1f}",
        "PITCH": f"{pitch:.1f}",
        "ROLL": f"{roll:.1f}",
    }


class HeadlessDashboard:
    """Keep startup/calibration automatic without OpenCV windows, with full interactive web dashboard."""

    def __init__(self):
        web_port = int(os.getenv("WEB_PORT", "8080"))
        self._web = DriverMonitorWebServer(port=web_port) if web_port > 0 else None

    def set_context(self, pipeline=None, esp=None, camera_source=None, cloud_sync=None, gps=None):
        if self._web:
            self._web.set_context(
                pipeline=pipeline,
                esp=esp,
                camera_source=camera_source,
                cloud_sync=cloud_sync,
                gps=gps,
            )

    def show_monitor(self, frame, state=None):
        if self._web:
            self._web.update_frame(frame, state)

    def show_alignment(self, frame, status, metrics, can_continue):
        if self._web and frame is not None:
            rendered = frame.copy()
            cv2.putText(rendered, f"ALIGNMENT: {status}", (12, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 215, 255), 2)
            self._web.update_frame(rendered, {
                "confirmed_alert": "CALIBRATING",
                "active_phase": "MEDIAPIPE_PHASE",
                "status": status,
                "mp_active": True,
            })

    def show_calibration(self, frame, data):
        if self._web and frame is not None:
            rendered = frame.copy()
            rem = float(data.get("remaining", 0.0))
            cv2.putText(rendered, f"MEDIAPIPE CALIBRATION ({rem:.1f}s)", (12, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 215, 255), 2)
            self._web.update_frame(rendered, {
                "confirmed_alert": "CALIBRATING",
                "active_phase": "MEDIAPIPE_PHASE",
                "mp_phase_remaining": round(rem, 2),
                "mp_ear": data.get("ear"),
                "mp_mar": data.get("mar"),
                "mp_active": True,
                "status": f"MediaPipe do baseline ({rem:.1f}s)",
            })

    def show_message(self, frame, title, body, can_start=False):
        if self._web and frame is not None:
            rendered = frame.copy()
            cv2.putText(rendered, str(title), (12, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            self._web.update_frame(rendered, {
                "confirmed_alert": "NORMAL",
                "status": f"{title}: {body}",
            })

    def poll_action(self):
        return None

    def close(self):
        if self._web is not None:
            self._web.close()
            self._web = None

    def __getattr__(self, name):
        if name.startswith("show_"):
            return lambda *args, **kwargs: None
        raise AttributeError(name)


def run():
    if os.getenv("GPS_ONLY", "0") == "1" or os.getenv("DISABLE_CAMERA", "0") == "1":
        print("[MAIN] Đang chạy ở chế độ GPS_ONLY=1: Đã ngắt camera và mô hình AI để tập trung kiểm thử GPS.")
        try:
            from AI.scripts.run_gps_standalone import main as run_gps_main
        except ImportError:
            # run_gps_standalone.py nằm trong AI/test/
            from AI.test.run_gps_standalone import main as run_gps_main
        run_gps_main()
        return

    model_path = Path(MODEL_PATH)

    has_display = bool(os.getenv("DISPLAY"))
    headless = os.getenv("HEADLESS", "0") == "1" or Dashboard is None or not has_display
    if not has_display and os.getenv("HEADLESS") != "1":
        print("[MAIN] Chay qua SSH/khong co man hinh (DISPLAY chua dat) -> Tu dong chuyen sang Headless Web Dashboard.")
    dashboard = HeadlessDashboard() if headless else Dashboard()

    esp = EspCommunicator(
        mode=os.getenv("ESP_MODE", "SERIAL"),
        port=os.getenv("ESP_PORT"),
        baudrate=int(os.getenv("ESP_BAUDRATE", "921600")),
        require_ack=os.getenv("ESP_REQUIRE_ACK", "1") == "1",
    )
    esp.send_status("NORMAL")
    cloud_sync = CloudSyncManager(esp_communicator=esp)
    cloud_sync.set_context(esp=esp)
    if hasattr(dashboard, "set_context"):
        dashboard.set_context(esp=esp, cloud_sync=cloud_sync)

    # TUYET DOI KHONG CHAY CAM MAY TINH (INDEX 0)
    # Quét camera ngoại (RTSP LAN / USB); nếu chưa sẵn sàng sau 3s sẽ tự động bắt ngầm qua CameraFrameSource
    cap = wait_for_external_camera(retry_interval=1.5, max_wait_seconds=3.0)

    landmarker = None
    pipeline = None
    camera_source = None
    event_recorder = None
    try:
        landmarker = create_face_landmarker(model_path)
        if landmarker is None:
            print("[STARTUP] MediaPipe unavailable; YOLO-only mode")
        if cap is not None and landmarker is not None and os.getenv("SKIP_CALIBRATION", "0") != "1":
            timestamp = -1
            stable_frames = 0
            can_continue = False

            alignment_deadline = time.monotonic() + float(os.getenv("ALIGNMENT_TIMEOUT", "30"))
            while True:
                if time.monotonic() >= alignment_deadline:
                    print("[CALIBRATION] Alignment timed out; use previous/default baseline")
                    break
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Khong doc duoc frame tu camera")
                timestamp = max(timestamp + 1, int(time.monotonic() * 1000))
                stable, metrics = _detect_alignment(frame, landmarker, timestamp)
                stable_frames = stable_frames + 1 if stable else 0
                can_continue = stable_frames >= STABLE_FRAMES_REQUIRED

                status = (
                    "Can chinh thanh cong - bat dau do baseline ca nhan"
                    if can_continue
                    else "Giu dau thang, nhin tu nhien vao camera"
                )

                dashboard.show_alignment(frame, status, metrics, can_continue)

                action = dashboard.poll_action()

                if action == "quit":
                    return

                if can_continue:
                    break

            if can_continue:
                # GIAI DOAN 2: Do baseline ca nhan bang MediaPipe (10~15s)
                session = CalibrationSession(duration=CALIBRATION_SECONDS)
                session.start()

                while not session.finished:
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError("Khong doc duoc frame khi do baseline")

                    data = session.update(frame, landmarker)
                    dashboard.show_calibration(frame, data)

                    if dashboard.poll_action() == "quit":
                        return

                # Luu ket qua vao baseline.json
                saved = session.save(BASELINE_PATH)
                if saved:
                    print(f"[CALIBRATION] Luu baseline ca nhan thanh cong: {BASELINE_PATH}")
                else:
                    print("[CALIBRATION] Khong du mau hop le de tao baseline moi, giu baseline hien tai.")

                # Dong MediaPipe ngay sau khi ket thuc 10~15s xac dinh de giai phong RAM/CPU cho YOLO
                try:
                    landmarker.close()
                except Exception:
                    pass
                landmarker = None

                # Thong bao hoan tat truoc khi khoi dong giam sat
                msg_end = time.monotonic() + 1.5
                while time.monotonic() < msg_end:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    dashboard.show_message(
                        frame,
                        "CALIBRATION COMPLETE",
                        "Do baseline ca nhan thanh cong!\nDang chuyen sang che do 100% YOLO...",
                        can_start=False,
                    )
                    if dashboard.poll_action() == "quit":
                        return

        # Chuyen ownership landmarker sang pipeline.
        # Pipeline se goi landmarker.close() trong pipeline.close().
        # Dat landmarker = None de finally block khong double-close.
        use_recorder = os.getenv("ENABLE_EVENT_RECORDER", "1") == "1"
        event_recorder = (
            EventRecorder(
                str(Path(__file__).resolve().parents[1] / "data"),
                cloud_sync=cloud_sync,
            )
            if use_recorder
            else None
        )
        # Không truyền on_frame=event_recorder.append_frame vào CameraFrameSource để tránh
        # 2 luồng (camera-capture và pipeline 30FPS) cùng chèn frame gây lệch thứ tự.
        # DrowsinessPipeline.process() là chủ sở hữu duy nhất đẩy frame theo đúng thứ tự đơn điệu.
        camera_source = CameraFrameSource(
            cap,
            on_frame=None,
            capture_factory=open_camera,
        )
        cap = None
        pipeline = DrowsinessPipeline(
            model_path=os.getenv("YOLO_MODEL_PATH", str(Path(__file__).resolve().parents[1] / "models" / "best.pt")),
            esp_communicator=esp,
            face_landmarker=landmarker,
            event_recorder=event_recorder,
            enable_event_recorder=use_recorder,
            cloud_sync=cloud_sync,
        )
        landmarker = None  # pipeline now owns the landmarker
        cloud_sync.set_context(esp=esp, pipeline=pipeline, camera_source=camera_source)
        if hasattr(dashboard, "set_context"):
            dashboard.set_context(pipeline=pipeline, esp=esp, camera_source=camera_source, cloud_sync=cloud_sync)

        fps_governor = EmbeddedFpsGovernor()
        last_frame_id = 0
        camera_source.set_max_fps(max(30.0, fps_governor.base_fps))
        while True:
            plan = fps_governor.wait_for_next_slot()
            ok, frame, frame_id, capture_timestamp_ns, capture_time_utc = camera_source.read_latest(
                after_frame_id=last_frame_id,
                timeout=1.0,
                allow_repeat=True,
            )
            if not ok:
                esp.send_status("NORMAL")
                pipeline.reset_tracking()
                dashboard.show_monitor(None, {"confirmed_alert": "NORMAL", "ai_fps": 0.0, "pipeline_latency_ms": 0.0})
                if dashboard.poll_action() == "quit":
                    return
                time.sleep(0.1)
                continue

            last_frame_id = frame_id
            rendered, state = pipeline.process(
                frame,
                frame_id=frame_id,
                capture_timestamp_ns=capture_timestamp_ns,
                capture_time_utc=capture_time_utc,
                plan=plan,
            )
            dashboard.show_monitor(rendered, state)

            if dashboard.poll_action() == "quit":
                return
    finally:
        if cloud_sync is not None:
            cloud_sync.close()
        if camera_source is not None:
            camera_source.close()
        if pipeline is not None:
            pipeline.close()
        if pipeline is None:
            if event_recorder is not None:
                event_recorder.close()
            if esp is not None:
                esp.close()
        if landmarker is not None:
            landmarker.close()
        if cap is not None:
            cap.release()
        dashboard.close()
        if not headless:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    run()
