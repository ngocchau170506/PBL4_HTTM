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

EYE_Z_THRESHOLD = -2.0
MOUTH_Z_THRESHOLD = 2.0

EYE_CANDIDATE_SECONDS = 2.0
YAWN_CANDIDATE_SECONDS = 1.0


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

face_landmarker = vision.FaceLandmarker.create_from_options(
    options
)


cap = cv2.VideoCapture(0)

start_time = time.time()

eye_start_time = None
mouth_start_time = None

eye_candidate = False
yawn_candidate = False


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

    current_time = time.time()

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

        eye_abnormal = z_ear < EYE_Z_THRESHOLD
        mouth_abnormal = z_mar > MOUTH_Z_THRESHOLD


        if eye_abnormal:

            if eye_start_time is None:
                eye_start_time = current_time

            eye_duration = current_time - eye_start_time

            if eye_duration >= EYE_CANDIDATE_SECONDS:
                eye_candidate = True

        else:

            eye_start_time = None
            eye_candidate = False

            eye_duration = 0.0


        if mouth_abnormal:

            if mouth_start_time is None:
                mouth_start_time = current_time

            mouth_duration = current_time - mouth_start_time

            if mouth_duration >= YAWN_CANDIDATE_SECONDS:
                yawn_candidate = True

        else:

            mouth_start_time = None
            yawn_candidate = False

            mouth_duration = 0.0


        cv2.putText(
            frame,
            f"EAR: {ear:.3f}",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 0),
            2
        )

        cv2.putText(
            frame,
            f"MAR: {mar:.3f}",
            (20, 65),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 0),
            2
        )

        cv2.putText(
            frame,
            f"Z_EAR: {z_ear:.2f}",
            (20, 95),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 0),
            2
        )

        cv2.putText(
            frame,
            f"Z_MAR: {z_mar:.2f}",
            (20, 125),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 0),
            2
        )

        if eye_start_time is not None:
            eye_duration = current_time - eye_start_time
        else:
            eye_duration = 0.0

        if mouth_start_time is not None:
            mouth_duration = current_time - mouth_start_time
        else:
            mouth_duration = 0.0


        cv2.putText(
            frame,
            f"Eye abnormal: {eye_duration:.1f}s",
            (20, 165),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 255) if eye_abnormal else (0, 255, 0),
            2
        )

        cv2.putText(
            frame,
            f"Mouth abnormal: {mouth_duration:.1f}s",
            (20, 195),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 255) if mouth_abnormal else (0, 255, 0),
            2
        )


        if eye_candidate and yawn_candidate:

            status = "EYE + YAWN CANDIDATE"
            status_color = (0, 0, 255)

        elif eye_candidate:

            status = "EYE CANDIDATE"
            status_color = (0, 0, 255)

        elif yawn_candidate:

            status = "YAWN CANDIDATE"
            status_color = (0, 165, 255)

        elif eye_abnormal:

            status = "EYE ABNORMAL"

            status_color = (0, 165, 255)

        elif mouth_abnormal:

            status = "MOUTH ABNORMAL"

            status_color = (0, 165, 255)

        else:

            status = "NORMAL"

            status_color = (0, 255, 0)


        cv2.putText(
            frame,
            status,
            (20, 240),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            status_color,
            2
        )


    else:

        eye_start_time = None
        mouth_start_time = None

        eye_candidate = False
        yawn_candidate = False

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
        "Driver Trigger Layer",
        frame
    )

    key = cv2.waitKey(1) & 0xFF

    if key == ord("q"):
        break


cap.release()
face_landmarker.close()
cv2.destroyAllWindows()