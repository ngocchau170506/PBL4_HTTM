import cv2
import time
from collections import deque
from ultralytics import YOLO


CAMERA_FPS = 30
BUFFER_FPS = 10
BUFFER_SIZE = 10

SAMPLE_INTERVAL = 1.0 / BUFFER_FPS


model = YOLO("models/best.pt")

frame_buffer = deque(maxlen=BUFFER_SIZE)

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("Không mở được camera")
    exit()


last_sample_time = 0

yawn_start_time = None
yawn_confirmed = False


while True:

    ret, frame = cap.read()

    if not ret:
        print("Không đọc được frame")
        break

    current_time = time.monotonic()

    # =========================
    # BUFFER
    # =========================

    if current_time - last_sample_time >= SAMPLE_INTERVAL:

        frame_buffer.append(frame.copy())

        last_sample_time = current_time


    # =========================
    # YOLO
    # =========================

    results = model.predict(
        source=frame,
        conf=0.1,
        verbose=False
    )

    result = results[0]

    yawn_detected = False

    for box in result.boxes:

        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        name = model.names[cls_id]

        print(
            f"Detected: {name} | confidence: {conf:.2f}"
        )

        if name.lower() == "yawn":
            yawn_detected = True


    # =========================
    # YAWN TIMER
    # =========================

    if yawn_detected:

        if yawn_start_time is None:
            yawn_start_time = current_time

        yawn_duration = current_time - yawn_start_time

        if yawn_duration >= 2.0:
            yawn_confirmed = True

    else:

        yawn_start_time = None
        yawn_confirmed = False


    # =========================
    # YAWN CONFIRMED
    # =========================

    if yawn_confirmed:

        cv2.putText(
            frame,
            "YAWN CONFIRMED",
            (20, 80),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 0, 255),
            3
        )

        cv2.putText(
            frame,
            f"Frames: {len(frame_buffer)}/{BUFFER_SIZE}",
            (20, 120),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2
        )


        if len(frame_buffer) == BUFFER_SIZE:

            print("==============================")
            print("YAWN CONFIRMED")
            print("10 FRAME SEQUENCE READY")
            print(f"Frames: {len(frame_buffer)}")
            print("==============================")


    # =========================
    # DISPLAY
    # =========================

    annotated_frame = result.plot()

    cv2.putText(
        annotated_frame,
        f"Buffer: {len(frame_buffer)}/{BUFFER_SIZE}",
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 0),
        2
    )


    if yawn_start_time is not None:

        yawn_duration = current_time - yawn_start_time

        cv2.putText(
            annotated_frame,
            f"Yawn: {yawn_duration:.1f}s",
            (20, 75),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 255),
            2
        )


    if yawn_confirmed:

        cv2.putText(
            annotated_frame,
            "YAWN CONFIRMED",
            (20, 110),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 0, 255),
            3
        )


    cv2.imshow(
        "YOLO26 + Frame Buffer",
        annotated_frame
    )


    if cv2.waitKey(1) & 0xFF == ord("q"):
        break


cap.release()
cv2.destroyAllWindows()