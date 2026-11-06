import time
import sys
import os
from pathlib import Path

import cv2
import mediapipe as mp

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from AI.app.dashboard import Dashboard
from AI.scripts.calibration import (
    BASELINE_PATH,
    CALIBRATION_SECONDS,
    MODEL_PATH,
    CalibrationSession,
    calculate_head_pose,
)
from AI.scripts.drowsiness_pipeline import DrowsinessPipeline
from AI.scripts.camera_frame_source import CameraFrameSource
from AI.scripts.esp_communication import EspCommunicator
from AI.scripts.event_recorder import EventRecorder
from AI.scripts.camera_config import open_camera

STABLE_FRAMES_REQUIRED = 15
# kết hợp với IoT thì thay return thành thông báo ra loa
def _detect_alignment(frame, landmarker, timestamp):
    """Detect face and head pose alignment in the given frame."""
    height, width = frame.shape[:2]
    image = mp.Image(
        image_format=mp.ImageFormat.SRGB,
        data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
    )
    result = landmarker.detect_for_video(image, timestamp)
    if not result.face_landmarks:
        return False, {"FACE": "CAN'T FIND"}
    pose = calculate_head_pose(result.face_landmarks[0], width, height)
    if pose is None:
        return False, {"FACE": "FIND", "POSE": "CAN'T CALC"}
    yaw, pitch, roll = pose
    stable = abs(yaw) <= 15 and abs(pitch) <= 15 and abs(roll) <= 10
    return stable, {
        "FACE": "OK", "YAW": f"{yaw:.1f}", "PITCH": f"{pitch:.1f}",
        "ROLL": f"{roll:.1f}",
    }

class HeadlessDashboard:
    """Keep startup/calibration automatic without OpenCV windows."""
    def poll_action(self):
        return None

    def close(self):
        pass

    def __getattr__(self, name):
        if name.startswith("show_"):
            return lambda *args, **kwargs: None
        raise AttributeError(name)


def run():
    model_path = Path(MODEL_PATH)
    cap = open_camera()
    if not cap.isOpened():
        raise RuntimeError("Cannot open camera")

    headless = os.getenv("HEADLESS", "0") == "1"
    dashboard = HeadlessDashboard() if headless else Dashboard()

    landmarker = None
    pipeline = None
    camera_source = None
    esp = None
    event_recorder = None
    try:
        try:
            options = mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                num_faces=1,
            )
            landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        except Exception as exc:
            print(f"[STARTUP] MediaPipe unavailable; YOLO-only mode: {exc}")
        if landmarker is not None and os.getenv("SKIP_CALIBRATION", "0") != "1":
            timestamp = -1
            stable_frames = 0
            can_continue = False

            alignment_deadline = time.monotonic() + float(os.getenv("ALIGNMENT_TIMEOUT", "30"))
            while True:
                if time.monotonic() >= alignment_deadline:
                    print("[CALIBRATION] Alignment timed out; use default baseline")
                    break
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Cannot read frame during alignment")
                timestamp = max(timestamp + 1, int(time.monotonic() * 1000))
                stable, metrics = _detect_alignment(frame, landmarker, timestamp)
                stable_frames = stable_frames + 1 if stable else 0
                can_continue = stable_frames >= STABLE_FRAMES_REQUIRED

                status = (
                    "Camera aligned, ready to calibrate"
                    if can_continue
                    else "Align your face to the camera and keep it stable for a few seconds..."
                )

                dashboard.show_alignment(frame, status, metrics, can_continue)

                action = dashboard.poll_action()

                if action == "quit":
                    return

                if can_continue:
                    break

            if can_continue:
                # GIAI DOAN 2: Do baseline ca nhan (Personal Calibration)
                session = CalibrationSession(duration=CALIBRATION_SECONDS)
                session.start()

                while not session.finished:
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError("Cannot read frame during calibration")

                    data = session.update(frame, landmarker)
                    dashboard.show_calibration(frame, data)

                    if dashboard.poll_action() == "quit":
                        return

                # Luu ket qua vao baseline.json
                saved = session.save(BASELINE_PATH)
                if saved:
                    print(f"[CALIBRATION] Saved baseline: {BASELINE_PATH}")
                else:
                    print("[CALIBRATION] Failed to save baseline")

                # Thong bao hoan tat truoc khi khoi dong giam sat
                msg_end = time.monotonic() + 1.5
                while time.monotonic() < msg_end:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    dashboard.show_message(
                        frame,
                        "CALIBRATION COMPLETE",
                        "Baseline saved successfully!\nStarting monitoring system...",
                        can_start=False,
                    )
                    if dashboard.poll_action() == "quit":
                        return

        # Giai doan 3: Khoi dong giam sat (Monitoring)
        # Thay đổi ESP_MODE từ MOCK sang UART để giao tiếp với ESP32
        esp = EspCommunicator(
            mode=os.getenv("ESP_MODE", "MOCK"),
            port=os.getenv("ESP_PORT"),
            baudrate=int(os.getenv("ESP_BAUDRATE", "115200")),
            require_ack=os.getenv("ESP_REQUIRE_ACK", "0") == "1",
        )
        esp.send_status("NORMAL")
        event_recorder = EventRecorder(str(Path(__file__).resolve().parents[1] / "data"))
        camera_source = CameraFrameSource(cap, on_frame=event_recorder.append_frame, capture_factory=open_camera)
        cap = None

        pipeline = DrowsinessPipeline(
            model_path=os.getenv("YOLO_MODEL_PATH", str(Path(__file__).resolve().parents[1] / "models" / "best.pt")),
            esp_communicator=esp,
            face_landmarker=landmarker,
            event_recorder=event_recorder,
            enable_event_recorder=False,
        )
        landmarker = None  # pipeline now owns the landmarker

        last_frame_id = 0
        while True:
            ok, frame, frame_id, capture_timestamp_ns, capture_time_utc = camera_source.read_latest(
                after_frame_id=last_frame_id,
            )
            if not ok:
                esp.send_status("NORMAL")
                pipeline.reset_tracking()
                dashboard.show_message(None, "CAMERA ERROR", camera_source.error or "Waiting for camera", can_start=False)
                if dashboard.poll_action() == "quit":
                    return
                continue

            last_frame_id = frame_id
            rendered, state = pipeline.process(
                frame,
                frame_id=frame_id,
                capture_timestamp_ns=capture_timestamp_ns,
                capture_time_utc=capture_time_utc,
            )
            dashboard.show_monitor(rendered, state)

            if dashboard.poll_action() == "quit":
                return
    finally:
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
