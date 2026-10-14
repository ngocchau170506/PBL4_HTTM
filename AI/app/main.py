"""Single application entry point for calibration and driver monitoring."""

import time
import sys
from pathlib import Path

import cv2
import mediapipe as mp

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from AI.app.dashboard import Dashboard
from AI.scripts.calibration import (
    BASELINE_PATH, MODEL_PATH, CalibrationSession, calculate_head_pose,
    validate_baseline,
)
from AI.scripts.drowsiness_pipeline import DrowsinessPipeline


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WINDOW_TITLE = "AI Driver Monitor"
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
    stable = abs(yaw) <= 25 and abs(pitch) <= 25 and abs(roll) <= 20
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
            status = ("Tu the on dinh - nhan TIEP TUC"
                      if can_continue else
                      "Giu dau thang, nhin tu nhien vao camera")
            dashboard.show_alignment(frame, status, metrics, can_continue)
            action = dashboard.poll_action()
            if action == "quit":
                return
            if action == "continue" and can_continue:
                break

        calibration = CalibrationSession()
        calibration.start()
        while not calibration.finished:
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Khong doc duoc frame trong hieu chuan")
            data = calibration.update(frame, landmarker)
            data["sample_count"] = calibration.sample_count
            dashboard.show_calibration(frame, data)
            if dashboard.poll_action() == "quit":
                return

        minimum_samples = 10
        if not calibration.save(BASELINE_PATH, minimum_samples=minimum_samples):
            dashboard.show_message(
                frame, "HIEU CHUAN THAT BAI",
                "Khong du mau hop le. Baseline cu duoc giu nguyen.",
            )
            while dashboard.poll_action() != "quit":
                time.sleep(0.05)
            return
        new_baseline = validate_baseline(
            BASELINE_PATH, minimum_samples=minimum_samples
        )
        if new_baseline is None:
            raise RuntimeError("Baseline vua luu khong hop le")

        while True:
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Khong doc duoc frame khi giam sat")
            dashboard.show_message(
                frame, "SAN SANG GIAM SAT",
                "Baseline hop le. Nhan BAT DAU GIAM SAT (hoac phim S).",
                can_start=True,
            )
            action = dashboard.poll_action()
            if action == "quit":
                return
            if action == "start":
                break


        pipeline = DrowsinessPipeline(landmarker, BASELINE_PATH)
        while True:
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Khong doc duoc frame khi giam sat")
            rendered, state = pipeline.process(frame)
            dashboard.show_monitor(rendered, state)
            if dashboard.poll_action() == "quit":
                return
    finally:
        if pipeline is not None:
            pipeline.close()
        if landmarker is not None:
            landmarker.close()
        cap.release()
        dashboard.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    run()
