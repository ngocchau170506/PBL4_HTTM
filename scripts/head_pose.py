import time
import math

import cv2
import mediapipe as mp
import numpy as np

MODEL_PATH = "models/face_landmarker.task"

# ---------------- MediaPipe ----------------
base_options = mp.tasks.BaseOptions(model_asset_path=MODEL_PATH)

options = mp.tasks.vision.FaceLandmarkerOptions(
    base_options=base_options,
    running_mode=mp.tasks.vision.RunningMode.VIDEO,
    num_faces=1,
)

face_landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)

# ---------------- Camera ----------------
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("Khong mo duoc camera!")
    raise SystemExit

dist_coeffs = np.zeros((4, 1))

# X sang phai, Y xuong duoi, Z ra xa camera. Goc toa do o dau mui.
model_points = np.array([
    [0.0,    0.0,    0.0],     
    [0.0,    330.0,  65.0],    
    [-225.0, -170.0, 135.0],  
    [225.0,  -170.0, 135.0],   
    [-150.0, 150.0,  125.0],   
    [150.0,  150.0,  125.0],   
], dtype=np.float64)

LANDMARK_IDS = [1, 152, 33, 263, 61, 291]

SMOOTH_ALPHA = 0.3  


def get_camera_matrix(w, h):
    focal = float(w)
    return np.array([
        [focal, 0, w / 2],
        [0, focal, h / 2],
        [0, 0, 1],
    ], dtype=np.float64)


def get_head_pose(landmarks, w, h):
    image_points = np.array(
        [[landmarks[i].x * w, landmarks[i].y * h] for i in LANDMARK_IDS],
        dtype=np.float64,
    )

    cam = get_camera_matrix(w, h)

    success, rvec, tvec = cv2.solvePnP(
        model_points,
        image_points,
        cam,
        dist_coeffs,
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not success:
        return None

    R, _ = cv2.Rodrigues(rvec)

    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)

    pitch = math.degrees(math.atan2(R[2, 1], R[2, 2]))
    yaw = math.degrees(math.atan2(-R[2, 0], sy))
    roll = math.degrees(math.atan2(R[1, 0], R[0, 0]))

    return yaw, pitch, roll, rvec, tvec, cam


def ema(prev, new, alpha=SMOOTH_ALPHA):
    if prev is None:
        return new
    return prev + alpha * (new - prev)


smooth = None  

while True:
    ret, frame = cap.read()
    if not ret:
        print("Khong doc duoc frame!")
        break

    frame = cv2.flip(frame, 1) 
    height, width = frame.shape[:2]

    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

    timestamp_ms = int(time.time() * 1000)
    result = face_landmarker.detect_for_video(mp_image, timestamp_ms)

    if result.face_landmarks:
        landmarks = result.face_landmarks[0]
        pose = get_head_pose(landmarks, width, height)

        if pose is not None:
            yaw, pitch, roll, rvec, tvec, cam = pose

            if smooth is None:
                smooth = (yaw, pitch, roll)
            else:
                smooth = (
                    ema(smooth[0], yaw),
                    ema(smooth[1], pitch),
                    ema(smooth[2], roll),
                )
            s_yaw, s_pitch, s_roll = smooth

            cv2.drawFrameAxes(frame, cam, dist_coeffs, rvec, tvec, 100)

            for i in LANDMARK_IDS:
                p = (int(landmarks[i].x * width), int(landmarks[i].y * height))
                cv2.circle(frame, p, 3, (0, 0, 255), -1)

            for text, y in (
                (f"Yaw:   {s_yaw:6.1f}", 40),
                (f"Pitch: {s_pitch:6.1f}", 70),
                (f"Roll:  {s_roll:6.1f}", 100),
            ):
                cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, (0, 255, 0), 2)

            print(f"Yaw: {s_yaw:.2f} | Pitch: {s_pitch:.2f} | Roll: {s_roll:.2f}")
    else:
        smooth = None 

    cv2.imshow("Head Pose Test", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
face_landmarker.close()
cv2.destroyAllWindows()