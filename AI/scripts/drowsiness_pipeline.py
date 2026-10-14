import cv2
import mediapipe as mp
import json
import time
import math
from collections import deque
from ultralytics import YOLO

YOLO_MODEL_PATH = "models/best.pt"
CONF_THRESHOLD = 0.1

yolo_model = YOLO(YOLO_MODEL_PATH)
print("YOLO loaded:", yolo_model.names)

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
last_yolo_alerts = None


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


DISPLAY_CAMERA_WIDTH = 840
DISPLAY_HEIGHT = 640
INFO_PANEL_WIDTH = 400


def fit_frame_to_size(frame, target_width, target_height):

    frame_height, frame_width = frame.shape[:2]
    scale = min(
        target_width / frame_width,
        target_height / frame_height
    )

    resized_width = max(1, int(frame_width * scale))
    resized_height = max(1, int(frame_height * scale))

    resized = cv2.resize(
        frame,
        (resized_width, resized_height)
    )

    top = (target_height - resized_height) // 2
    bottom = target_height - resized_height - top
    left = (target_width - resized_width) // 2
    right = target_width - resized_width - left

    return cv2.copyMakeBorder(
        resized,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_CONSTANT,
        value=(10, 10, 10)
    )


def draw_info_panel(
    height,
    width,
    face_detected,
    ear,
    mar,
    eye_state,
    eye_duration,
    mouth_state,
    mouth_duration,
    head_status,
    detections,
    buffer_count,
    combined_status
):

    panel = cv2.UMat(
        height,
        width,
        cv2.CV_8UC3
    ).get()
    panel[:] = (18, 22, 28)

    white = (235, 240, 245)
    muted = (145, 158, 170)
    cyan = (220, 190, 70)
    green = (90, 215, 125)
    amber = (40, 190, 255)
    red = (60, 70, 235)
    card = (31, 37, 45)
    border = (55, 64, 75)

    def draw_card(x1, y1, x2, y2, title, accent=cyan):
        cv2.rectangle(
            panel,
            (x1, y1),
            (x2, y2),
            card,
            -1
        )
        cv2.rectangle(
            panel,
            (x1, y1),
            (x2, y2),
            border,
            1
        )
        cv2.rectangle(
            panel,
            (x1, y1),
            (x1 + 4, y2),
            accent,
            -1
        )
        cv2.putText(
            panel,
            title,
            (x1 + 15, y1 + 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            muted,
            1,
            cv2.LINE_AA
        )

    def draw_value(label, value, x, y, color=white):
        cv2.putText(
            panel,
            label,
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.40,
            muted,
            1,
            cv2.LINE_AA
        )
        cv2.putText(
            panel,
            value,
            (x, y + 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            color,
            1,
            cv2.LINE_AA
        )

    def status_color(value):
        if value in {
            "EYE DROWSY",
            "YAWNING",
            "DISTRACTED HEAD"
        }:
            return red
        if value in {"NO FACE", "MOUTH OPEN", "EYE CLOSED"}:
            return amber
        return green

    cv2.putText(
        panel,
        "AI DRIVER MONITOR",
        (18, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        white,
        2,
        cv2.LINE_AA
    )
    cv2.putText(
        panel,
        "REAL-TIME SAFETY DASHBOARD",
        (20, 49),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.32,
        muted,
        1,
        cv2.LINE_AA
    )
    cv2.circle(
        panel,
        (width - 25, 25),
        7,
        green,
        -1
    )

    overall_color = green if combined_status == "NORMAL" else red
    draw_card(12, 64, width - 12, 125, "OVERALL STATUS", overall_color)
    cv2.putText(
        panel,
        combined_status,
        (27, 101),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        overall_color,
        2,
        cv2.LINE_AA
    )
    cv2.putText(
        panel,
        "SYSTEM ACTIVE",
        (width - 125, 103),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.34,
        muted,
        1,
        cv2.LINE_AA
    )

    draw_card(12, 137, 194, 265, "EYE MONITORING", red
              if eye_state == "EYE DROWSY" else cyan)
    draw_value(
        "EAR",
        f"{ear:.3f}" if ear is not None else "N/A",
        25,
        171,
        white
    )
    draw_value(
        "STATE",
        eye_state,
        25,
        221,
        status_color(eye_state)
    )
    cv2.putText(
        panel,
        f"CLOSED  {eye_duration:.2f}s",
        (25, 253),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.38,
        muted,
        1,
        cv2.LINE_AA
    )

    draw_card(206, 137, width - 12, 265, "MOUTH MONITORING", red
              if mouth_state == "YAWNING" else cyan)
    draw_value(
        "MAR",
        f"{mar:.3f}" if mar is not None else "N/A",
        219,
        171,
        white
    )
    draw_value(
        "STATE",
        mouth_state,
        219,
        221,
        status_color(mouth_state)
    )
    cv2.putText(
        panel,
        f"OPEN  {mouth_duration:.2f}s",
        (219, 253),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.38,
        muted,
        1,
        cv2.LINE_AA
    )

    head_color = red if head_status == "DISTRACTED HEAD" else green
    draw_card(12, 277, width - 12, 330, "HEAD MONITORING", head_color)
    cv2.putText(
        panel,
        head_status,
        (27, 310),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        head_color,
        1,
        cv2.LINE_AA
    )
    cv2.putText(
        panel,
        "FACE DETECTED" if face_detected else "NO FACE",
        (width - 145, 310),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.36,
        green if face_detected else amber,
        1,
        cv2.LINE_AA
    )

    draw_card(12, 342, width - 12, 515, "OBJECT DETECTION", cyan)
    cv2.putText(
        panel,
        "CLASS",
        (27, 369),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.34,
        muted,
        1,
        cv2.LINE_AA
    )
    cv2.putText(
        panel,
        "CONF.",
        (width - 73, 369),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.34,
        muted,
        1,
        cv2.LINE_AA
    )

    if detections:
        for index, (class_name, confidence) in enumerate(detections[:5]):
            row_y = 394 + index * 22
            detection_color = amber if class_name.casefold() in {
                "phone",
                "smoking"
            } else white
            cv2.circle(
                panel,
                (29, row_y - 5),
                3,
                detection_color,
                -1
            )
            cv2.putText(
                panel,
                class_name.upper(),
                (40, row_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.39,
                detection_color,
                1,
                cv2.LINE_AA
            )
            cv2.putText(
                panel,
                f"{confidence:.2f}",
                (width - 68, row_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.39,
                white,
                1,
                cv2.LINE_AA
            )
    else:
        cv2.putText(
            panel,
            "NO OBJECTS DETECTED",
            (27, 402),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.40,
            muted,
            1,
            cv2.LINE_AA
        )

    cv2.rectangle(
        panel,
        (12, 535),
        (width - 12, 592),
        card,
        -1
    )
    cv2.putText(
        panel,
        "FRAME BUFFER",
        (25, 558),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.40,
        muted,
        1,
        cv2.LINE_AA
    )
    cv2.putText(
        panel,
        f"{buffer_count}/{BUFFER_SIZE}",
        (width - 92, 559),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.54,
        white,
        1,
        cv2.LINE_AA
    )
    cv2.rectangle(
        panel,
        (25, 572),
        (width - 25, 580),
        (55, 64, 75),
        -1
    )
    progress_width = int(
        (width - 50) * min(buffer_count / BUFFER_SIZE, 1)
    )
    cv2.rectangle(
        panel,
        (25, 572),
        (25 + progress_width, 580),
        cyan,
        -1
    )

    cv2.line(
        panel,
        (12, 610),
        (width - 12, 610),
        border,
        1
    )
    cv2.putText(
        panel,
        "Q  QUIT",
        (20, 630),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.36,
        muted,
        1,
        cv2.LINE_AA
    )
    cv2.putText(
        panel,
        "CAMERA ONLINE",
        (width - 132, 630),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.36,
        green,
        1,
        cv2.LINE_AA
    )

    return panel


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

        # ==================================
        # YOLO DETECTION
        # ==================================

        yolo_results = yolo_model.predict(
            frame,
            conf=CONF_THRESHOLD,
            imgsz=640,
            verbose=False
        )

        detected_classes = []
        detected_objects = []

        for detection in yolo_results:
            for box in detection.boxes:
                class_id = int(box.cls[0])
                confidence = float(box.conf[0])
                class_name = str(
                    yolo_model.names[class_id]
                ).strip()
                normalized_class_name = class_name.casefold()

                x1, y1, x2, y2 = map(
                    int,
                    box.xyxy[0].tolist()
                )

                detected_classes.append(normalized_class_name)
                detected_objects.append(
                    (class_name, confidence)
                )

                cv2.rectangle(
                    frame,
                    (x1, y1),
                    (x2, y2),
                    (255, 165, 0),
                    2
                )

                cv2.putText(
                    frame,
                    f"{class_name} {confidence:.2f}",
                    (x1, max(y1 - 10, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 165, 0),
                    2
                )


        yolo_classes = set(detected_classes)

        yolo_alerts = {
            name
            for name in yolo_classes
            if name in {
                "distracted head",
                "phone",
                "smoking"
            }
        }

        if yolo_alerts != last_yolo_alerts:

            if yolo_alerts:
                print(
                    "[YOLO] Detected: "
                    + ", ".join(sorted(yolo_alerts))
                )
            elif last_yolo_alerts:
                print("[YOLO] Phone/Smoking alerts cleared")

            last_yolo_alerts = yolo_alerts


        # ==================================
        # FACE DETECTED
        # ==================================

        ear = None
        mar = None
        eye_duration = 0
        mouth_duration = 0
        eye_state = "NO FACE"
        mouth_state = "NO FACE"

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

            if eye_triggered:
                eye_state = "EYE DROWSY"
            elif ear < EAR_THRESHOLD:
                eye_state = "EYE CLOSED"
            else:
                eye_state = "EYE OPEN"

            if yawn_triggered:
                mouth_state = "YAWNING"
            elif mar > MAR_THRESHOLD:
                mouth_state = "MOUTH OPEN"
            else:
                mouth_state = "MOUTH CLOSED"

        # ==================================
        # NO FACE
        # ==================================

        else:

            eye_closed_start = None
            mouth_open_start = None

            eye_triggered = False
            yawn_triggered = False

        # ==================================
        # COMBINED STATUS
        # ==================================

        alerts = []

        if eye_triggered:
            alerts.append("EYE DROWSY")

        if yawn_triggered:
            alerts.append("MOUTH OPEN")

        if "distracted head" in yolo_classes:
            alerts.append("DISTRACTED HEAD")

        if "phone" in yolo_classes:
            alerts.append("PHONE")

        if "smoking" in yolo_classes:
            alerts.append("SMOKING")

        combined_status = (
            " | ".join(alerts)
            if alerts
            else "NORMAL"
        )

        head_status = (
            "DISTRACTED HEAD"
            if "distracted head" in yolo_classes
            else "NORMAL"
        )

        camera_view = fit_frame_to_size(
            frame,
            DISPLAY_CAMERA_WIDTH,
            DISPLAY_HEIGHT
        )

        info_panel = draw_info_panel(
            camera_view.shape[0],
            INFO_PANEL_WIDTH,
            bool(result.face_landmarks),
            ear,
            mar,
            eye_state,
            eye_duration,
            mouth_state,
            mouth_duration,
            head_status,
            detected_objects,
            len(frame_buffer),
            combined_status
        )

        dashboard = cv2.hconcat(
            [camera_view, info_panel]
        )

        cv2.imshow(
            "Drowsiness Pipeline",
            dashboard
        )


        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):

            break


cap.release()

cv2.destroyAllWindows()