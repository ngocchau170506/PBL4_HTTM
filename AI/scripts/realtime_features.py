import cv2
import mediapipe as mp
import json
import math
import time

from mediapipe.tasks import python
from mediapipe.tasks.python import vision


BASELINE_PATH = "calibration/baseline.json"
MODEL_PATH = "models/face_landmarker.task"

LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]

MOUTH_LEFT = 61
MOUTH_RIGHT = 291
MOUTH_TOP = 13
MOUTH_BOTTOM = 14


def distance(p1, p2):
    return math.sqrt(
        (p1.x - p2.x) ** 2 +
        (p1.y - p2.y) ** 2
    )


def calculate_ear(landmarks, eye_indices):
    p1 = landmarks[eye_indices[0]]
    p2 = landmarks[eye_indices[1]]
    p3 = landmarks[eye_indices[2]]
    p4 = landmarks[eye_indices[3]]
    p5 = landmarks[eye_indices[4]]
    p6 = landmarks[eye_indices[5]]

    vertical_1 = distance(p2, p6)
    vertical_2 = distance(p3, p5)
    horizontal = distance(p1, p4)

    if horizontal == 0:
        return 0.0

    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def calculate_mar(landmarks):
    left = landmarks[MOUTH_LEFT]
    right = landmarks[MOUTH_RIGHT]
    top = landmarks[MOUTH_TOP]
    bottom = landmarks[MOUTH_BOTTOM]

    horizontal = distance(left, right)
    vertical = distance(top, bottom)

    if horizontal == 0:
        return 0.0

    return vertical / horizontal


def calculate_z_score(value, mean, std):
    if std <= 0:
        return 0.0

    return (value - mean) / std


def load_baseline():
    with open(BASELINE_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


baseline = load_baseline()

ear_mean = baseline["ear"]["mean"]
ear_std = baseline["ear"]["std"]

mar_mean = baseline["mar"]["mean"]
mar_std = baseline["mar"]["std"]


print("===== DRIVER BASELINE =====")
print(f"EAR mean : {ear_mean:.4f}")
print(f"EAR std  : {ear_std:.4f}")
print(f"MAR mean : {mar_mean:.4f}")
print(f"MAR std  : {mar_std:.4f}")
print("============================")


base_options = python.BaseOptions(
    model_asset_path=MODEL_PATH
)

options = vision.FaceLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.VIDEO,
    num_faces=1
)

face_landmarker = vision.FaceLandmarker.create_from_options(options)
#"rtsp://192.168.1.10:554/user=admin_password=tlJwpbo6_channel=1_stream=0.sdp?real_stream"
cap = cv2.VideoCapture(0)

start_time = time.time()

while True:

    ret, frame = cap.read()

    if not ret:
        break

    rgb_frame = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2RGB
    )

    mp_image = mp.Image(
        image_format=mp.ImageFormat.SRGB,
        data=rgb_frame
    )

    timestamp_ms = int(
        (time.time() - start_time) * 1000
    )

    result = face_landmarker.detect_for_video(
        mp_image,
        timestamp_ms
    )

    if result.face_landmarks:

        landmarks = result.face_landmarks[0]

        ear_left = calculate_ear(
            landmarks,
            LEFT_EYE
        )

        ear_right = calculate_ear(
            landmarks,
            RIGHT_EYE
        )

        ear = (ear_left + ear_right) / 2.0

        mar = calculate_mar(landmarks)

        z_ear = calculate_z_score(
            ear,
            ear_mean,
            ear_std
        )

        z_mar = calculate_z_score(
            mar,
            mar_mean,
            mar_std
        )

        cv2.putText(
            frame,
            f"EAR: {ear:.3f}",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2
        )

        cv2.putText(
            frame,
            f"MAR: {mar:.3f}",
            (20, 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2
        )

        cv2.putText(
            frame,
            f"Z_EAR: {z_ear:.2f}",
            (20, 105),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 0),
            2
        )

        cv2.putText(
            frame,
            f"Z_MAR: {z_mar:.2f}",
            (20, 140),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 0),
            2
        )

        if z_ear < -2.0:
            eye_status = "EYE DEVIATION"
        else:
            eye_status = "EYE NORMAL"

        if z_mar > 2.0:
            mouth_status = "MOUTH DEVIATION"
        else:
            mouth_status = "MOUTH NORMAL"

        eye_color = (
            (0, 0, 255)
            if z_ear < -2.0
            else (0, 255, 0)
        )

        mouth_color = (
            (0, 0, 255)
            if z_mar > 2.0
            else (0, 255, 0)
        )

        cv2.putText(
            frame,
            eye_status,
            (20, 180),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            eye_color,
            2
        )

        cv2.putText(
            frame,
            mouth_status,
            (20, 215),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            mouth_color,
            2
        )

    else:

        cv2.putText(
            frame,
            "NO FACE",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2
        )

    cv2.imshow(
        "Realtime Driver Features",
        frame
    )

    key = cv2.waitKey(1) & 0xFF

    if key == ord("q"):
        break


cap.release()
face_landmarker.close()
cv2.destroyAllWindows()