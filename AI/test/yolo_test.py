import cv2
import time
import numpy as np
from ultralytics import YOLO

model = YOLO("AI/models/best.pt")
cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("Khong mo duoc camera")
    exit()

cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

yawn_start_time = None
yawn_confirmed = False
prev_time = time.time()
fps = 0.0

PANEL_WIDTH = 370
CONF_THRESHOLD = 0.05
YAWN_THRESHOLD = 2.0

def normalize_name(name):
    return name.lower().replace("-", "").replace("_", "").replace(" ", "")

def draw_text(img, text, pos, scale=0.55, color=(230, 235, 245), thickness=1):
    cv2.putText(
        img, str(text), pos,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale, color, thickness, cv2.LINE_AA
    )

def draw_bar(img, x, y, width, height, value, color):
    value = max(0.0, min(float(value), 1.0))

    cv2.rectangle(
        img, (x, y), (x + width, y + height),
        (45, 53, 68), -1
    )

    fill_width = int(width * value)

    if fill_width > 0:
        cv2.rectangle(
            img, (x, y),
            (x + fill_width, y + height),
            color, -1
        )

while True:
    ret, frame = cap.read()

    if not ret:
        print("Khong doc duoc frame")
        break

    now = time.time()
    instant_fps = 1.0 / max(now - prev_time, 1e-6)
    fps = instant_fps if fps == 0 else 0.8 * fps + 0.2 * instant_fps
    prev_time = now

    results = model.predict(
    source=frame,
    imgsz=960,
    conf=0.05,
    verbose=False
)

    result = results[0]
    detections = {}

    for box in result.boxes:
        cls_id = int(box.cls[0].item())
        conf = float(box.conf[0].item())
        name = model.names[cls_id]

        print(f"Detected: {name} | confidence: {conf:.2f}")

        detections[name] = max(detections.get(name, 0.0), conf)

    # Kiem tra dau hieu ngap / mo mieng
    yawn_detected = any(
        normalize_name(name) in [
            "yawn", "yawning", "openmouth", "mouthopen"
        ]
        for name in detections
    )

    if yawn_detected:
        if yawn_start_time is None:
            yawn_start_time = now

        yawn_duration = now - yawn_start_time
        yawn_confirmed = yawn_duration >= YAWN_THRESHOLD
    else:
        yawn_start_time = None
        yawn_confirmed = False
        yawn_duration = 0.0

    # Anh camera co khung nhan dien YOLO
    camera_view = result.plot()

    # Tao bang thong tin ben phai
    panel = np.zeros((camera_view.shape[0], PANEL_WIDTH, 3), dtype=np.uint8)
    panel[:] = (25, 30, 42)

    # Tieu de
    draw_text(panel, "DRIVER SAFETY MONITOR", (18, 30), 0.62,
              (255, 255, 255), 2)

    cv2.line(panel, (15, 45), (PANEL_WIDTH - 15, 45),
             (75, 85, 105), 1)

    # Trang thai tong hop
    draw_text(panel, "CURRENT DRIVER STATUS", (18, 72),
              0.48, (175, 190, 210))

    if yawn_confirmed:
        status = "WARNING: YAWN"
        status_color = (60, 60, 255)
    elif yawn_detected:
        status = "MONITORING YAWN"
        status_color = (0, 200, 255)
    else:
        status = "NO YAWN DETECTED"
        status_color = (70, 210, 120)

    cv2.rectangle(panel, (15, 84), (PANEL_WIDTH - 15, 125),
                  status_color, -1)
    draw_text(panel, status, (25, 110), 0.58,
              (255, 255, 255), 2)

    # Thoi gian ngap
    draw_text(panel, "Yawn duration", (18, 153), 0.5)
    draw_text(panel, f"{yawn_duration:.1f} / {YAWN_THRESHOLD:.1f} sec",
              (210, 153), 0.48, (0, 220, 255), 1)

    draw_bar(
        panel, 18, 164, PANEL_WIDTH - 36, 10,
        min(yawn_duration / YAWN_THRESHOLD, 1.0),
        status_color
    )

    # Cac lop YOLO phat hien trong frame hien tai
    draw_text(panel, "DETECTION CONFIDENCE", (18, 205),
              0.52, (255, 255, 255), 2)

    sorted_detections = sorted(
        detections.items(), key=lambda item: item[1], reverse=True
    )

    if not sorted_detections:
        draw_text(panel, "No object detected", (18, 235),
                  0.48, (160, 170, 190))
    else:
        for i, (name, conf) in enumerate(sorted_detections[:6]):
            y = 235 + i * 42

            draw_text(panel, name[:24], (18, y),
                      0.46, (225, 230, 240))

            draw_text(panel, f"{conf * 100:.1f}%",
                      (PANEL_WIDTH - 65, y), 0.45,
                      (255, 255, 255))

            color = (
                (70, 210, 120) if conf >= 0.7
                else (0, 190, 255) if conf >= 0.4
                else (130, 150, 180)
            )

            draw_bar(panel, 18, y + 8, PANEL_WIDTH - 36, 8, conf, color)

    # Trang thai rieng cho cac nhom neu model co cac lop tuong ung
    base_y = 355
    draw_text(panel, "FEATURE SUMMARY", (18, base_y),
              0.52, (255, 255, 255), 2)

    normalized = {normalize_name(k): v for k, v in detections.items()}

    feature_groups = [
        ("Eye", ["eyeopen", "eyeclosed", "eyesopen", "eyesclosed"]),
        ("Mouth", ["mouthopen", "openmouth", "yawn", "yawning"]),
        ("Head", ["distractedhead", "headpose"]),
        ("Phone", ["phone"]),
        ("Smoking", ["smoking"])
    ]

    for i, (label, aliases) in enumerate(feature_groups):
        y = base_y + 27 + i * 22
        found = [
            (name, conf) for name, conf in normalized.items()
            if name in aliases
        ]

        if found:
            name, conf = max(found, key=lambda item: item[1])
            value = f"{name} ({conf * 100:.0f}%)"
            color = (70, 210, 120)
        else:
            value = "Not detected"
            color = (145, 155, 175)

        draw_text(panel, f"{label}:", (18, y), 0.43)
        draw_text(panel, value[:27], (100, y), 0.40, color)

    # FPS va huong dan
    cv2.line(panel, (15, 472), (PANEL_WIDTH - 15, 472),
             (75, 85, 105), 1)
    draw_text(panel, f"FPS: {fps:.1f}", (18, 465),
              0.42, (180, 220, 255))

    # Ghep camera va bang thanh mot cua so
    combined = np.hstack((camera_view, panel))

    cv2.imshow("YOLO26 Driver Drowsiness", combined)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()