import cv2
import mediapipe as mp

MODEL_PATH = "models/face_landmarker.task"

BaseOptions = mp.tasks.BaseOptions
FaceLandmarker = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
RunningMode = mp.tasks.vision.RunningMode

options = FaceLandmarkerOptions(
    base_options=BaseOptions(
        model_asset_path=MODEL_PATH
    ),
    running_mode=RunningMode.VIDEO, 
    #sau khi hoàn thiện phần còn lại thì chuyển thành LIVE_STREAM
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
        rbg_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rbg_frame)
        results = landmarker.detect_for_video(mp_image, timestamp_ms)

        timestamp_ms += 33

        if results.face_landmarks:
            landmarks = results.face_landmarks[0]
            cv2.putText(
                frame,
                f"Landmasks: {len(landmarks)}",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 255, 0),
                2
            )
        cv2.imshow("Face Landmarker", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
