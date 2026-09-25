import cv2
import mediapipe as mp
import numpy as np
import json
import os

MODEL_PATH = "models/face_landmarker.task"
BASELINE_PATH = "calibration/baseline.json"

K_EAR = 2.0
K_MAR = 2.0

LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]

MOUTH = {
    "left": 61,
    "right": 291,
    "top": 13,
    "bottom": 14
}

BaseOptions = mp.tasks.BaseOptions
FaceLandmarker = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
RunningMode = mp.tasks.vision.RunningMode


def landmark_to_point(landmark, width, height):
    return np.array([
        landmark.x * width,
        landmark.y * height
    ])


def distance(p1, p2):
    return np.linalg.norm(p1 - p2)


def calculate_ear(landmarks, eye_indices, width, height):
    p1 = landmark_to_point(landmarks[eye_indices[0]], width, height)
    p2 = landmark_to_point(landmarks[eye_indices[1]], width, height)
    p3 = landmark_to_point(landmarks[eye_indices[2]], width, height)
    p4 = landmark_to_point(landmarks[eye_indices[3]], width, height)
    p5 = landmark_to_point(landmarks[eye_indices[4]], width, height)
    p6 = landmark_to_point(landmarks[eye_indices[5]], width, height)

    vertical_1 = distance(p2, p6)
    vertical_2 = distance(p3, p5)
    horizontal = distance(p1, p4)

    if horizontal == 0:
        return 0.0

    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def calculate_mar(landmarks, width, height):
    left = landmark_to_point(
        landmarks[MOUTH["left"]],
        width,
        height
    )

    right = landmark_to_point(
        landmarks[MOUTH["right"]],
        width,
        height
    )

    top = landmark_to_point(
        landmarks[MOUTH["top"]],
        width,
        height
    )

    bottom = landmark_to_point(
        landmarks[MOUTH["bottom"]],
        width,
        height
    )

    horizontal = distance(left, right)
    vertical = distance(top, bottom)

    if horizontal == 0:
        return 0.0

    return vertical / horizontal


if not os.path.exists(BASELINE_PATH):
    print("baseline.json not found.")
    print("Please run calibration first.")
    exit()


with open(
    BASELINE_PATH,
    "r",
    encoding="utf-8"
) as file:
    baseline = json.load(file)


ear_mean = baseline["ear"]["mean"]
ear_std = baseline["ear"]["std"]

mar_mean = baseline["mar"]["mean"]
mar_std = baseline["mar"]["std"]


ear_threshold = ear_mean - K_EAR * ear_std
mar_threshold = mar_mean + K_MAR * mar_std


print("Baseline loaded")
print()
print(f"EAR mean      : {ear_mean:.4f}")
print(f"EAR std       : {ear_std:.4f}")
print(f"EAR threshold : {ear_threshold:.4f}")
print()
print(f"MAR mean      : {mar_mean:.4f}")
print(f"MAR std       : {mar_std:.4f}")
print(f"MAR threshold : {mar_threshold:.4f}")
print()


options = FaceLandmarkerOptions(
    base_options=BaseOptions(
        model_asset_path=MODEL_PATH
    ),
    running_mode=RunningMode.VIDEO,
    num_faces=1
)

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("Cannot open camera.")
    exit()

timestamp_ms = 0


with FaceLandmarker.create_from_options(options) as landmarker:

    while cap.isOpened():

        ret, frame = cap.read()

        if not ret:
            break

        frame = cv2.flip(frame, 1)

        height, width = frame.shape[:2]

        rgb_frame = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2RGB
        )

        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb_frame
        )

        result = landmarker.detect_for_video(
            mp_image,
            timestamp_ms
        )

        timestamp_ms += 33

        if result.face_landmarks:

            landmarks = result.face_landmarks[0]

            left_ear = calculate_ear(
                landmarks,
                LEFT_EYE,
                width,
                height
            )

            right_ear = calculate_ear(
                landmarks,
                RIGHT_EYE,
                width,
                height
            )

            ear = (left_ear + right_ear) / 2.0

            mar = calculate_mar(
                landmarks,
                width,
                height
            )

            if ear < ear_threshold:
                eye_status = "EYE CLOSED"
            else:
                eye_status = "EYE OPEN"

            if mar > mar_threshold:
                mouth_status = "MOUTH OPEN"
            else:
                mouth_status = "MOUTH NORMAL"

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
                f"EAR Threshold: {ear_threshold:.3f}",
                (20, 75),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"MAR: {mar:.3f}",
                (20, 110),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"Eye: {eye_status}",
                (20, 155),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"Mouth: {mouth_status}",
                (20, 195),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2
            )

        else:

            cv2.putText(
                frame,
                "NO FACE",
                (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 0, 255),
                2
            )

        cv2.imshow(
            "Realtime Face Features",
            frame
        )

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break


cap.release()
cv2.destroyAllWindows()