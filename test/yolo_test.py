import cv2
import time
from ultralytics import YOLO

model = YOLO("models/best.pt")

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("Không mở được camera")
    exit()

yawn_start_time = None
yawn_confirmed = False

while True:
    ret, frame = cap.read()

    if not ret:
        print("Không đọc được frame")
        break

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

        print(f"Detected: {name} | confidence: {conf:.2f}")

        if name.lower() in ["yawn", "open-mouth"]:
            yawn_detected = True

    # Xử lý thời gian yawn
    if yawn_detected:
        if yawn_start_time is None:
            yawn_start_time = time.time()

        yawn_duration = time.time() - yawn_start_time

        if yawn_duration >= 2:
            yawn_confirmed = True

    else:
        yawn_start_time = None
        yawn_confirmed = False

    # Hiển thị trạng thái
    if yawn_start_time is not None:
        yawn_duration = time.time() - yawn_start_time
        cv2.putText(
            frame,
            f"Yawn: {yawn_duration:.1f}s",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 255),
            2
        )

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

    annotated_frame = result.plot()

    # Ghi trạng thái lên frame đã annotate
    if yawn_start_time is not None:
        yawn_duration = time.time() - yawn_start_time

        cv2.putText(
            annotated_frame,
            f"Yawn: {yawn_duration:.1f}s",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 255),
            2
        )

    if yawn_confirmed:
        cv2.putText(
            annotated_frame,
            "YAWN CONFIRMED",
            (20, 80),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 0, 255),
            3
        )

    cv2.imshow("YOLO26 Driver Drowsiness", annotated_frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()