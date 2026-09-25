import cv2
import mediapipe as mp
import json
import time
import math
from collections import deque


BASELINE_PATH = "calibration/baseline.json"

EYE_CLOSED_SECONDS = 2.0
YAWN_SECONDS = 1.0

K_EAR = 2.0
K_MAR = 2.0

BUFFER_FPS = 10
BUFFER_SIZE = 10

SAMPLE_INTERVAL = 1.0 / BUFFER_FPS


def distance(p1, p2):
    return math.sqrt(
        (p1.x - p2.x) ** 2 +
        (p1.y - p2.y) ** 2
    )


def calculate_ear(landmarks):

    left = [33, 160, 158, 133, 153, 144]
    right = [362, 385, 387, 263, 373, 380]

    def eye_ear(points):

        p1, p2, p3, p4, p5, p6 = [
            landmarks[i] for i in points
        ]

        vertical_1 = distance(p2, p6)
        vertical_2 = distance(p3, p5)
        horizontal = distance(p1, p4)

        return (
            vertical_1 + vertical_2
        ) / (2.0 * horizontal)

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


# ==========================================
# LOAD BASELINE
# ==========================================

with open(BASELINE_PATH, "r") as f:
    baseline = json.load(f)


ear_mean = baseline["ear"]["mean"]
ear_std = baseline["ear"]["std"]

mar_mean = baseline["mar"]["mean"]
mar_std = baseline["mar"]["std"]


EAR_THRESHOLD = ear_mean - K_EAR * ear_std
MAR_THRESHOLD = mar_mean + K_MAR * mar_std


# ==========================================
# FRAME BUFFER
# ==========================================

frame_buffer = deque(maxlen=BUFFER_SIZE)

last_sample_time = 0


# ==========================================
# TIMER
# ==========================================

eye_closed_start = None
mouth_open_start = None

eye_triggered = False
yawn_triggered = False


# ==========================================
# TEMPORAL SEQUENCE
# ==========================================

sequence_ready = False
sequence_type = None


def get_sequence():

    if len(frame_buffer) < BUFFER_SIZE:
        return None

    return list(frame_buffer)


def process_trigger(trigger_type):

    global sequence_ready
    global sequence_type

    sequence = get_sequence()

    if sequence is None:
        print(
            f"[{trigger_type}] "
            f"Buffer chưa đủ frame: "
            f"{len(frame_buffer)}/{BUFFER_SIZE}"
        )
        return

    sequence_ready = True
    sequence_type = trigger_type

    print()
    print("===================================")
    print("       TEMPORAL SEQUENCE READY")
    print("===================================")
    print(f"Trigger: {trigger_type}")
    print(f"Frames: {len(sequence)}")
    print(f"Duration: {len(sequence) / BUFFER_FPS:.1f}s")
    print("Sequence:")
    
    for i in range(len(sequence)):
        print(f"  Frame {i + 1}")

    print("===================================")
    print()


# ==========================================
# MEDIAPIPE
# ==========================================

BaseOptions = mp.tasks.BaseOptions

FaceLandmarker = mp.tasks.vision.FaceLandmarker

FaceLandmarkerOptions = (
    mp.tasks.vision.FaceLandmarkerOptions
)

VisionRunningMode = mp.tasks.vision.RunningMode


options = FaceLandmarkerOptions(
    base_options=BaseOptions(
        model_asset_path="models/face_landmarker.task"
    ),
    running_mode=VisionRunningMode.VIDEO,
    num_faces=1
)


# ==========================================
# CAMERA
# ==========================================

cap = cv2.VideoCapture(0)

if not cap.isOpened():

    print("Cannot open camera")
    exit()


frame_timestamp = 0


print("===================================")
print("       DROWSINESS PIPELINE")
print("===================================")
print(f"EAR threshold: {EAR_THRESHOLD:.3f}")
print(f"MAR threshold: {MAR_THRESHOLD:.3f}")
print(f"Eye trigger: {EYE_CLOSED_SECONDS}s")
print(f"Yawn trigger: {YAWN_SECONDS}s")
print(f"Buffer: {BUFFER_SIZE} frames")
print(f"Buffer FPS: {BUFFER_FPS}")
print("===================================")


with FaceLandmarker.create_from_options(options) as landmarker:

    while True:

        ret, frame = cap.read()

        if not ret:

            print("Cannot read camera")
            break


        # ==================================
        # BUFFER SAMPLING
        # ==================================

        current_time = time.monotonic()

        if (
            current_time - last_sample_time
            >= SAMPLE_INTERVAL
        ):

            frame_buffer.append(
                frame.copy()
            )

            last_sample_time = current_time


        # ==================================
        # MEDIAPIPE
        # ==================================

        frame_timestamp += 33

        rgb = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2RGB
        )

        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb
        )

        result = landmarker.detect_for_video(
            mp_image,
            frame_timestamp
        )


        status = "NORMAL"


        # ==================================
        # FACE DETECTED
        # ==================================

        if result.face_landmarks:

            landmarks = result.face_landmarks[0]

            ear = calculate_ear(
                landmarks
            )

            mar = calculate_mar(
                landmarks
            )


            # ==================================
            # EYE TIMER
            # ==================================

            if ear < EAR_THRESHOLD:

                if eye_closed_start is None:

                    eye_closed_start = current_time

                eye_duration = (
                    current_time
                    - eye_closed_start
                )

                if (
                    eye_duration
                    >= EYE_CLOSED_SECONDS
                ):

                    if not eye_triggered:

                        eye_triggered = True

                        process_trigger(
                            "EYE"
                        )

            else:

                eye_closed_start = None
                eye_triggered = False


            # ==================================
            # YAWN TIMER
            # ==================================

            if mar > MAR_THRESHOLD:

                if mouth_open_start is None:

                    mouth_open_start = current_time

                mouth_duration = (
                    current_time
                    - mouth_open_start
                )

                if (
                    mouth_duration
                    >= YAWN_SECONDS
                ):

                    if not yawn_triggered:

                        yawn_triggered = True

                        process_trigger(
                            "YAWN"
                        )

            else:

                mouth_open_start = None
                yawn_triggered = False


            # ==================================
            # STATUS
            # ==================================

            if eye_triggered and yawn_triggered:

                status = "EYE + YAWN"

            elif eye_triggered:

                status = "EYE TRIGGER"

            elif yawn_triggered:

                status = "YAWN TRIGGER"

            elif eye_closed_start is not None:

                status = "EYE CLOSED"

            elif mouth_open_start is not None:

                status = "MOUTH OPEN"


            # ==================================
            # DISPLAY
            # ==================================

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
                f"MAR: {mar:.3f}",
                (20, 70),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"Buffer: {len(frame_buffer)}/{BUFFER_SIZE}",
                (20, 105),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 255),
                2
            )


            if eye_closed_start is not None:

                eye_duration = (
                    current_time
                    - eye_closed_start
                )

            else:

                eye_duration = 0


            if mouth_open_start is not None:

                mouth_duration = (
                    current_time
                    - mouth_open_start
                )

            else:

                mouth_duration = 0


            cv2.putText(
                frame,
                f"Eye: {eye_duration:.2f}s",
                (20, 140),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"Yawn: {mouth_duration:.2f}s",
                (20, 175),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"STATUS: {status}",
                (20, 220),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 0, 255)
                if "TRIGGER" in status
                else (0, 255, 0),
                2
            )


        # ==================================
        # NO FACE
        # ==================================

        else:

            eye_closed_start = None
            mouth_open_start = None

            eye_triggered = False
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


        cv2.imshow(
            "Drowsiness Pipeline",
            frame
        )


        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):

            break


cap.release()

cv2.destroyAllWindows()