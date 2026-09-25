import cv2
import mediapipe as mp
import json
import time
import math


BASELINE_PATH = "calibration/baseline.json"

EYE_CLOSED_SECONDS = 2.0
YAWN_SECONDS = 1.0

K_EAR = 2.0
K_MAR = 2.0


def distance(p1, p2):
    return math.sqrt(
        (p1.x - p2.x) ** 2 +
        (p1.y - p2.y) ** 2
    )


def calculate_ear(landmarks):
    left = [33, 160, 158, 133, 153, 144]
    right = [362, 385, 387, 263, 373, 380]

    def eye_ear(points):
        p1, p2, p3, p4, p5, p6 = [landmarks[i] for i in points]

        vertical_1 = distance(p2, p6)
        vertical_2 = distance(p3, p5)
        horizontal = distance(p1, p4)

        return (vertical_1 + vertical_2) / (2.0 * horizontal)

    left_ear = eye_ear(left)
    right_ear = eye_ear(right)

    return (left_ear + right_ear) / 2.0


def calculate_mar(landmarks):
    left = landmarks[61]
    right = landmarks[291]
    top = landmarks[13]
    bottom = landmarks[14]

    vertical = distance(top, bottom)
    horizontal = distance(left, right)

    return vertical / horizontal


with open(BASELINE_PATH, "r") as f:
    baseline = json.load(f)


ear_mean = baseline["ear"]["mean"]
ear_std = baseline["ear"]["std"]

mar_mean = baseline["mar"]["mean"]
mar_std = baseline["mar"]["std"]


EAR_THRESHOLD = ear_mean - K_EAR * ear_std
MAR_THRESHOLD = mar_mean + K_MAR * mar_std


print("===================================")
print("      DRIVER MONITORING SYSTEM")
print("===================================")
print(f"EAR threshold: {EAR_THRESHOLD:.3f}")
print(f"MAR threshold: {MAR_THRESHOLD:.3f}")
print(f"Eye closed trigger: {EYE_CLOSED_SECONDS}s")
print(f"Yawn trigger: {YAWN_SECONDS}s")
print("===================================")


BaseOptions = mp.tasks.BaseOptions
FaceLandmarker = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode


options = FaceLandmarkerOptions(
    base_options=BaseOptions(
        model_asset_path="models/face_landmarker.task"
    ),
    running_mode=VisionRunningMode.VIDEO,
    num_faces=1
)


eye_closed_start = None
eye_triggered = False

mouth_open_start = None
yawn_triggered = False


cap = cv2.VideoCapture(0)

start_time = time.monotonic()
frame_timestamp = 0


with FaceLandmarker.create_from_options(options) as landmarker:

    while True:

        ret, frame = cap.read()

        if not ret:
            print("Cannot read camera")
            break

        frame_timestamp += 33

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb
        )

        result = landmarker.detect_for_video(
            mp_image,
            frame_timestamp
        )

        current_time = time.monotonic()

        status = "NORMAL"

        if result.face_landmarks:

            landmarks = result.face_landmarks[0]

            ear = calculate_ear(landmarks)
            mar = calculate_mar(landmarks)

            # =========================
            # EYE CLOSURE
            # =========================

            if ear < EAR_THRESHOLD:

                if eye_closed_start is None:
                    eye_closed_start = current_time

                eye_closed_duration = current_time - eye_closed_start

                if eye_closed_duration >= EYE_CLOSED_SECONDS:
                    eye_triggered = True

            else:

                eye_closed_start = None
                eye_triggered = False

            # =========================
            # YAWN DETECTION
            # =========================

            if mar > MAR_THRESHOLD:

                if mouth_open_start is None:
                    mouth_open_start = current_time

                mouth_open_duration = current_time - mouth_open_start

                if mouth_open_duration >= YAWN_SECONDS:
                    yawn_triggered = True

            else:

                mouth_open_start = None
                yawn_triggered = False

            # =========================
            # STATUS
            # =========================

            if eye_triggered and yawn_triggered:
                status = "EYE + YAWN TRIGGER"

            elif eye_triggered:
                status = "EYE TRIGGER"

            elif yawn_triggered:
                status = "YAWN TRIGGER"

            elif eye_closed_start is not None:
                status = "EYE CLOSED"

            elif mouth_open_start is not None:
                status = "MOUTH OPEN"

            # =========================
            # DISPLAY
            # =========================

            cv2.putText(
                frame,
                f"EAR: {ear:.3f}",
                (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"EAR TH: {EAR_THRESHOLD:.3f}",
                (20, 65),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"MAR: {mar:.3f}",
                (20, 100),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"MAR TH: {MAR_THRESHOLD:.3f}",
                (20, 130),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 0),
                2
            )

            if eye_closed_start is not None:

                eye_duration = current_time - eye_closed_start

                cv2.putText(
                    frame,
                    f"Eye closed: {eye_duration:.2f}s",
                    (20, 165),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 255),
                    2
                )

            else:

                cv2.putText(
                    frame,
                    "Eye closed: 0.00s",
                    (20, 165),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 255),
                    2
                )

            if mouth_open_start is not None:

                mouth_duration = current_time - mouth_open_start

                cv2.putText(
                    frame,
                    f"Mouth open: {mouth_duration:.2f}s",
                    (20, 200),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 255),
                    2
                )

            else:

                cv2.putText(
                    frame,
                    "Mouth open: 0.00s",
                    (20, 200),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 255),
                    2
                )

            cv2.putText(
                frame,
                f"STATUS: {status}",
                (20, 245),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 0, 255) if "TRIGGER" in status else (0, 255, 0),
                2
            )

        else:

            eye_closed_start = None
            eye_triggered = False

            mouth_open_start = None
            yawn_triggered = False

            cv2.putText(
                frame,
                "NO FACE",
                (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 0, 255),
                2
            )

        cv2.imshow("Driver Monitoring", frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break


cap.release()
cv2.destroyAllWindows()