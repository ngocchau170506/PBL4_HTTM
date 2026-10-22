import cv2
import mediapipe as mp
import numpy as np

MODEL_PATH = "models/face_landmarker.task"

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

    ear = (vertical_1 + vertical_2) / (2.0 * horizontal)

    return ear


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

    mar = vertical / horizontal

    return mar


def draw_points(frame, landmarks, indices):
    height, width = frame.shape[:2]

    for index in indices:
        point = landmark_to_point(
            landmarks[index],
            width,
            height
        )

        x = int(point[0])
        y = int(point[1])

        cv2.circle(
            frame,
            (x, y),
            3,
            (0, 255, 0),
            -1
        )


options = FaceLandmarkerOptions(
    base_options=BaseOptions(
        model_asset_path=MODEL_PATH
    ),
    running_mode=RunningMode.VIDEO,
    num_faces=1
)

cap = cv2.VideoCapture(0)

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

            draw_points(
                frame,
                landmarks,
                LEFT_EYE
            )

            draw_points(
                frame,
                landmarks,
                RIGHT_EYE
            )

            draw_points(
                frame,
                landmarks,
                list(MOUTH.values())
            )

            cv2.putText(
                frame,
                f"EAR: {ear:.3f}",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"MAR: {mar:.3f}",
                (20, 75),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2
            )

        cv2.imshow(
            "Face Features",
            frame
        )

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

cap.release()
cv2.destroyAllWindows()