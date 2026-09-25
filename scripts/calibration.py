import cv2
import mediapipe as mp
import numpy as np
import json
import time
import os

MODEL_PATH = "models/face_landmarker.task"
OUTPUT_PATH = "calibration/baseline.json"

CALIBRATION_SECONDS = 30
SAMPLE_INTERVAL = 0.1

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

    return (
        vertical_1 + vertical_2
    ) / (2.0 * horizontal)


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


def robust_filter(values):
    values = np.array(values, dtype=np.float64)

    if len(values) < 5:
        return values

    median = np.median(values)

    mad = np.median(
        np.abs(values - median)
    )

    if mad < 1e-6:
        return values

    robust_z = np.abs(
        values - median
    ) / (1.4826 * mad)

    return values[robust_z < 3.5]


def draw_text(frame, text, position, scale=0.7):
    cv2.putText(
        frame,
        text,
        position,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (0, 255, 0),
        2
    )


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

ear_samples = []
mar_samples = []

last_sample_time = 0

start_time = time.time()

os.makedirs(
    os.path.dirname(OUTPUT_PATH),
    exist_ok=True
)

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

        elapsed = time.time() - start_time

        remaining = CALIBRATION_SECONDS - elapsed

        draw_text(
            frame,
            "FACE CALIBRATION",
            (20, 40),
            0.9
        )

        draw_text(
            frame,
            "Look straight and keep eyes normally open",
            (20, 75),
            0.6
        )

        if remaining > 0:

            draw_text(
                frame,
                f"Time remaining: {remaining:.1f}s",
                (20, 110),
                0.7
            )

            if result.face_landmarks:

                landmarks = result.face_landmarks[0]

                ear_left = calculate_ear(
                    landmarks,
                    LEFT_EYE,
                    width,
                    height
                )

                ear_right = calculate_ear(
                    landmarks,
                    RIGHT_EYE,
                    width,
                    height
                )

                ear = (
                    ear_left +
                    ear_right
                ) / 2.0

                mar = calculate_mar(
                    landmarks,
                    width,
                    height
                )

                current_time = time.time()

                if current_time - last_sample_time >= SAMPLE_INTERVAL:

                    ear_samples.append(ear)
                    mar_samples.append(mar)

                    last_sample_time = current_time

                draw_text(
                    frame,
                    f"EAR: {ear:.3f}",
                    (20, 150)
                )

                draw_text(
                    frame,
                    f"MAR: {mar:.3f}",
                    (20, 185)
                )

                draw_text(
                    frame,
                    "Face detected",
                    (20, 220)
                )

            else:

                draw_text(
                    frame,
                    "No face detected",
                    (20, 150),
                    0.7
                )

        else:
            break

        cv2.imshow(
            "Face Calibration",
            frame
        )

        if cv2.waitKey(1) & 0xFF == ord("q"):
            cap.release()
            cv2.destroyAllWindows()
            exit()

cap.release()
cv2.destroyAllWindows()


if len(ear_samples) < 20:
    print("Calibration failed: not enough valid samples.")
    exit()

filtered_ear = robust_filter(ear_samples)
filtered_mar = robust_filter(mar_samples)

ear_mean = float(np.mean(filtered_ear))
ear_std = float(np.std(filtered_ear))

mar_mean = float(np.mean(filtered_mar))
mar_std = float(np.std(filtered_mar))

baseline = {
    "calibration_duration": CALIBRATION_SECONDS,
    "samples": {
        "raw": len(ear_samples),
        "valid_ear": len(filtered_ear),
        "valid_mar": len(filtered_mar)
    },
    "ear": {
        "mean": ear_mean,
        "std": ear_std
    },
    "mar": {
        "mean": mar_mean,
        "std": mar_std
    }
}

with open(
    OUTPUT_PATH,
    "w",
    encoding="utf-8"
) as file:
    json.dump(
        baseline,
        file,
        indent=4
    )

print()
print("Calibration completed.")
print()
print(f"EAR mean : {ear_mean:.4f}")
print(f"EAR std  : {ear_std:.4f}")
print()
print(f"MAR mean : {mar_mean:.4f}")
print(f"MAR std  : {mar_std:.4f}")
print()
print(f"Saved to: {OUTPUT_PATH}")