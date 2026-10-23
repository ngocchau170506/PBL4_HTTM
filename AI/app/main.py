"""Single application entry point for calibration and driver monitoring."""

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

WINDOW_TITLE = "Assess the driver's condition"
STABLE_FRAMES_REQUIRED = 15


def _detect_alignment(frame, landmarker, timestamp):
    height, width = frame.shape[:2]
    image = mp.Image(
        image_format=mp.ImageFormat.SRGB,
        data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
    )
    result = landmarker.detect_for_video(image, timestamp)
    if not result.face_landmarks:
        return False, {"FACE": "KHONG THAY"}
    pose = calculate_head_pose(result.face_landmarks[0], width, height)
    if pose is None:
        return False, {"FACE": "THAY", "POSE": "KHONG TIN CAY"}
    yaw, pitch, roll = pose
    stable = abs(yaw) <= 15 and abs(pitch) <= 15 and abs(roll) <= 10
    return stable, {
        "FACE": "OK", "YAW": f"{yaw:.1f}", "PITCH": f"{pitch:.1f}",
        "ROLL": f"{roll:.1f}",
    }


def run():
    model_path = Path(MODEL_PATH)
    if not model_path.is_file():
        raise FileNotFoundError(f"Khong tim thay MediaPipe model: {model_path}")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Khong mo duoc camera")

    dashboard = Dashboard()
    landmarker = None
    pipeline = None
    camera_source = None
    try:
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=1,
        )
        landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        timestamp = -1
        stable_frames = 0

        while True:
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

        # GIAI DOAN 2: Do baseline ca nhan (Personal Calibration)
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

        # Thong bao hoan tat truoc khi khoi dong giam sat
        msg_end = time.monotonic() + 1.5
        while time.monotonic() < msg_end:
            ok, frame = cap.read()
            if not ok:
                break
            dashboard.show_message(
                frame,
                "CALIBRATION COMPLETE",
                "Do baseline ca nhan thanh cong!\nDang khoi dong he thong giam sat...",
                can_start=False,
            )
            if dashboard.poll_action() == "quit":
                return

        # Chuyen ownership landmarker sang pipeline.
        # Pipeline se goi landmarker.close() trong pipeline.close().
        # Dat landmarker = None de finally block khong double-close.
        esp = EspCommunicator(
            mode=os.getenv("ESP_MODE", "MOCK"),
            port=os.getenv("ESP_PORT"),
            baudrate=int(os.getenv("ESP_BAUDRATE", "115200")),
        )
        event_recorder = EventRecorder(str(Path(__file__).resolve().parents[1] / "data"))
        camera_source = CameraFrameSource(cap, on_frame=event_recorder.append_frame)
        cap = None
        pipeline = DrowsinessPipeline(
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
                raise RuntimeError(
                    "Khong doc duoc frame khi giam sat"
                )

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
        if landmarker is not None:
            landmarker.close()
        if cap is not None:
            cap.release()
        dashboard.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    run()
