import cv2
import mediapipe as mp
import json
import math
import time
import statistics
import numpy as np
import os

from mediapipe.tasks import python
from mediapipe.tasks.python import vision


MODEL_PATH = "models/face_landmarker.task"
BASELINE_DIR = "calibration"
BASELINE_PATH = os.path.join(BASELINE_DIR, "baseline.json")

COUNTDOWN_SECONDS = 3
CALIBRATION_SECONDS = 30
SAMPLE_INTERVAL = 0.1

LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]

MOUTH_LEFT = 61
MOUTH_RIGHT = 291
MOUTH_TOP = 13
MOUTH_BOTTOM = 14

MODEL_POINTS = np.array([
    [0.0,    0.0,    0.0],    
    [0.0,    330.0,  65.0],    
    [-225.0, -170.0, 135.0],   
    [225.0,  -170.0, 135.0],  
    [-150.0, 150.0,  125.0],   
    [150.0,  150.0,  125.0],  
], dtype=np.float64)

POSE_LANDMARK_IDS = [1, 152, 33, 263, 61, 291]
DIST_COEFFS = np.zeros((4, 1))


def distance(p1, p2, w, h):
    """Khoang cach theo pixel"""
    return math.hypot((p1.x - p2.x) * w, (p1.y - p2.y) * h)


def calculate_ear(landmarks, idx, w, h):
    p1, p2, p3, p4, p5, p6 = [landmarks[i] for i in idx]

    vertical_1 = distance(p2, p6, w, h)
    vertical_2 = distance(p3, p5, w, h)
    horizontal = distance(p1, p4, w, h)

    if horizontal == 0:
        return 0.0

    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def calculate_mar(landmarks, w, h):
    horizontal = distance(landmarks[MOUTH_LEFT], landmarks[MOUTH_RIGHT], w, h)
    vertical = distance(landmarks[MOUTH_TOP], landmarks[MOUTH_BOTTOM], w, h)

    if horizontal == 0:
        return 0.0

    return vertical / horizontal


dist_coeffs = np.zeros((4, 1))

model_points = np.array([
    [0.0,    0.0,    0.0],
    [0.0,    330.0,  65.0],
    [-225.0, -170.0, 135.0],
    [225.0,  -170.0, 135.0],
    [-150.0, 150.0,  125.0],
    [150.0,  150.0,  125.0],
], dtype=np.float64)

LANDMARK_IDS = [1, 152, 33, 263, 61, 291]


def get_camera_matrix(width, height):

    focal = float(width)

    return np.array([
        [focal, 0, width / 2],
        [0, focal, height / 2],
        [0, 0, 1]
    ], dtype=np.float64)


def calculate_head_pose(landmarks, width, height):

    image_points = np.array(
        [
            [
                landmarks[index].x * width,
                landmarks[index].y * height
            ]
            for index in LANDMARK_IDS
        ],
        dtype=np.float64
    )

    camera_matrix = get_camera_matrix(
        width,
        height
    )

    success, rvec, tvec = cv2.solvePnP(
        model_points,
        image_points,
        camera_matrix,
        dist_coeffs,
        flags=cv2.SOLVEPNP_ITERATIVE
    )

    if not success:
        return None

    rotation_matrix, _ = cv2.Rodrigues(rvec)

    sy = math.sqrt(
        rotation_matrix[0, 0] ** 2 +
        rotation_matrix[1, 0] ** 2
    )

    pitch = math.degrees(
        math.atan2(
            rotation_matrix[2, 1],
            rotation_matrix[2, 2]
        )
    )

    yaw = math.degrees(
        math.atan2(
            -rotation_matrix[2, 0],
            sy
        )
    )

    roll = math.degrees(
        math.atan2(
            rotation_matrix[1, 0],
            rotation_matrix[0, 0]
        )
    )

    return yaw, pitch, roll


def robust_filter(values):
    if len(values) < 5:
        return values

    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])

    if mad == 0:
        return values

    threshold = 3.5 * mad
    return [v for v in values if abs(v - median) <= threshold]


def calculate_statistics(values):
    filtered = robust_filter(values)

    if len(filtered) == 0:
        return {"mean": 0.0, "std": 0.0}

    if len(filtered) == 1:
        return {"mean": round(filtered[0], 4), "std": 0.0}

    return {
        "mean": round(statistics.mean(filtered), 4),
        "std": round(statistics.stdev(filtered), 4),
    }


def put_text(frame, text, y, color=(0, 255, 0), scale=0.7):
    cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX,
                scale, color, 2)


def main():
    base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
    options = vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
    )
    face_landmarker = vision.FaceLandmarker.create_from_options(options)

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Khong mo duoc camera!")
        face_landmarker.close()
        return

    print()
    print("======================================")
    print("       DRIVER CALIBRATION")
    print("======================================")
    print()
    print("Keep your head in a normal driving position.")
    print("Look naturally at the camera.")
    print("Do not intentionally close your eyes.")
    print("Do not intentionally open your mouth.")
    print()

    ear_values, mar_values = [], []
    yaw_values, pitch_values, roll_values = [], [], []
    sample_count = 0

    program_start = time.time()
    calib_start = None
    last_sample_time = 0.0
    last_ts = -1

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Khong doc duoc frame!")
            break

        now = time.time()
        since_start = now - program_start

        if since_start < COUNTDOWN_SECONDS:
            put_text(frame, f"Starting in {COUNTDOWN_SECONDS - since_start:.1f}s",
                     40, (0, 255, 255), 0.8)
            put_text(frame, "Keep normal driving position", 80, (255, 255, 0))
            cv2.imshow("Driver Calibration", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            continue

        if calib_start is None:
            calib_start = now

        elapsed = now - calib_start
        if elapsed >= CALIBRATION_SECONDS:
            break

        height, width = frame.shape[:2]

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        ts = int((now - program_start) * 1000)
        if ts <= last_ts:
            ts = last_ts + 1
        last_ts = ts

        result = face_landmarker.detect_for_video(mp_image, ts)

        if result.face_landmarks and now - last_sample_time >= SAMPLE_INTERVAL:
            landmarks = result.face_landmarks[0]

            pose = calculate_head_pose(landmarks, width, height)

            if pose is not None:
                ear_left = calculate_ear(landmarks, LEFT_EYE, width, height)
                ear_right = calculate_ear(landmarks, RIGHT_EYE, width, height)
                ear = (ear_left + ear_right) / 2.0
                mar = calculate_mar(landmarks, width, height)
                yaw, pitch, roll = pose

                ear_values.append(ear)
                mar_values.append(mar)
                yaw_values.append(yaw)
                pitch_values.append(pitch)
                roll_values.append(roll)

                sample_count += 1
                last_sample_time = now

        remaining = CALIBRATION_SECONDS - elapsed
        put_text(frame, f"Calibration: {remaining:.1f}s", 40, scale=0.8)
        put_text(frame, f"Samples: {sample_count}", 75)
        put_text(frame, "Keep normal driving position", 115, (255, 255, 0))

        if not result.face_landmarks:
            put_text(frame, "NO FACE DETECTED", 155, (0, 0, 255))

        cv2.imshow("Driver Calibration", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    face_landmarker.close()
    cv2.destroyAllWindows()

    if sample_count == 0:
        print("Khong thu duoc mau nao. Khong ghi baseline.")
        return

    ear_stats = calculate_statistics(ear_values)
    mar_stats = calculate_statistics(mar_values)
    yaw_stats = calculate_statistics(yaw_values)
    pitch_stats = calculate_statistics(pitch_values)
    roll_stats = calculate_statistics(roll_values)

    baseline = {
        "calibration_duration": CALIBRATION_SECONDS,
        "samples": {
            "raw": sample_count,
            "valid_ear": len(robust_filter(ear_values)),
            "valid_mar": len(robust_filter(mar_values)),
            "valid_head_pose": len(robust_filter(yaw_values)),
        },
        "ear": ear_stats,
        "mar": mar_stats,
        "head_pose": {
            "yaw": yaw_stats,
            "pitch": pitch_stats,
            "roll": roll_stats,
        },
    }

    os.makedirs(BASELINE_DIR, exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as file:
        json.dump(baseline, file, indent=4, ensure_ascii=False)

    print()
    print("======================================")
    print("       CALIBRATION COMPLETED")
    print("======================================")
    print()
    print("EAR:")
    print(f"  Mean = {ear_stats['mean']}")
    print(f"  Std  = {ear_stats['std']}")
    print()
    print("MAR:")
    print(f"  Mean = {mar_stats['mean']}")
    print(f"  Std  = {mar_stats['std']}")
    print()
    print("HEAD POSE:")
    print(f"  Yaw   = {yaw_stats['mean']} +/- {yaw_stats['std']}")
    print(f"  Pitch = {pitch_stats['mean']} +/- {pitch_stats['std']}")
    print(f"  Roll  = {roll_stats['mean']} +/- {roll_stats['std']}")
    print()
    print(f"Saved to: {BASELINE_PATH}")


if __name__ == "__main__":
    main()