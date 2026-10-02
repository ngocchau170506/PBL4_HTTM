import cv2
import time
from collections import deque


CAMERA_FPS = 30
BUFFER_FPS = 10
BUFFER_SIZE = 10

SAMPLE_INTERVAL = 1.0 / BUFFER_FPS


frame_buffer = deque(maxlen=BUFFER_SIZE)



cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("Cannot open camera")
    exit()


last_sample_time = 0


print("===================================")
print("          FRAME BUFFER TEST")
print("===================================")
print(f"Camera FPS: {CAMERA_FPS}")
print(f"Buffer FPS: {BUFFER_FPS}")
print(f"Buffer size: {BUFFER_SIZE}")
print(f"Sequence duration: {BUFFER_SIZE / BUFFER_FPS:.1f}s")
print("Press Q to exit")
print("===================================")


while True:

    ret, frame = cap.read()

    if not ret:
        print("Cannot read camera")
        break

    current_time = time.monotonic()

    if current_time - last_sample_time >= SAMPLE_INTERVAL:

        frame_buffer.append(frame.copy())

        last_sample_time = current_time

    cv2.putText(
        frame,
        f"Buffer: {len(frame_buffer)}/{BUFFER_SIZE}",
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 0),
        2
    )

    cv2.putText(
        frame,
        f"Sampling: {BUFFER_FPS} FPS",
        (20, 75),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 255),
        2
    )

    cv2.imshow("Frame Buffer", frame)

    key = cv2.waitKey(1) & 0xFF

    if key == ord("q"):
        break


cap.release()
cv2.destroyAllWindows()