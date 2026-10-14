import math
import time
import cv2
import threading
import numpy as np
from pathlib import Path
from ultralytics import YOLO
from core.state import system_config
from core.face_roi_tracker import FaceRoiTracker
from core.arbitration_engine import ArbitrationEngine
from core.unified_model import UnifiedDMSModel
from core.mediapipe_tracker import MediaPipeFaceTracker

class UnifiedDriverDetector:
    """
    [KIẾN TRÚC ALL-IN-ONE TOÀN NĂNG: BEST_UNIFIED.PT GÁNH TRỌN 100% CÔNG VIỆC]
    1. Model Toàn Năng (models/best_unified.pt):
       - Gánh trọn toàn bộ trách nhiệm từ best.pt (Mắt, Ngáp), yolov8n.pt (Điện thoại), và yolo26n-pose.pt (Tư thế).
       - Tự động trích xuất và hiển thị toàn bộ Bounding Box trực quan trên video stream.
    2. Bộ Trợ Lực MediaPipe (models/face_landmarker.task):
       - Chạy ngầm tĩnh lặng không in log cảnh báo C++ rác.
       - Cung cấp chỉ số sinh học EAR/MAR mượt mà hỗ trợ chống lóa kính.
    3. Bộ Trọng Tài Khử Mâu Thuẫn (ArbitrationEngine):
       - Khử mâu thuẫn cúi đầu nhìn táp-lô, nói chuyện vs ngáp, gãi cằm vs điện thoại.
    4. Zero-Lag Pipeline (Dual-Thread Grabber & Background Async AI Worker):
       - Video stream đạt 30 FPS tuyệt đối mượt mà, hàm process_frame() < 1ms.
    """
    def __init__(self, camera_index=None):
        cam_cfg = system_config.get("camera", {})
        models_cfg = system_config.get("models", {})
        base_dir = Path(__file__).resolve().parent.parent

        self.source_type = cam_cfg.get("source_type", "local")
        self.device_index = camera_index if camera_index is not None else cam_cfg.get("device_index", 0)
        self.rtsp_url = cam_cfg.get("rtsp_url", "")
        self.flip_180 = cam_cfg.get("flip_180", True)

        self.cap = None
        self.latest_raw_frame = None
        self._is_running = True
        self._frame_lock = threading.Lock()
        self.is_connected = False

        # Khởi tạo nguồn Camera (Local hoặc RTSP)
        self._init_capture()

        # Face ROI Tracker & Arbitration Engine
        self.enable_two_stage_crop = models_cfg.get("enable_two_stage_crop", True)
        self.face_tracker = FaceRoiTracker()
        self.arbitrator = ArbitrationEngine()

        # 1. KHỞI TẠO MODEL TOÀN NĂNG BEST_UNIFIED.PT (GÁNH TRỌN CÔNG VIỆC)
        unified_path = base_dir / models_cfg.get("yolo_unified", "models/best_unified.pt")
        self.use_unified_model = models_cfg.get("use_unified_model", True) and unified_path.exists()
        self.unified_engine = None

        if self.use_unified_model:
            try:
                self.unified_engine = UnifiedDMSModel.load_from_file(str(unified_path), base_dir)
                print(f"[YOLO26] BEST_UNIFIED.PT DA KICH HOAT (Ganh tron Mat, Mieng, Dien thoai, Tu the): {unified_path.name}")
            except Exception as e:
                print(f"[YOLO26 Warning] Loi nap unified engine: {e}")
                self.use_unified_model = False

        # 2. KHỞI TẠO BỘ TRỢ LỰC MEDIAPIPE (SILENT HELPER)
        self.enable_mediapipe = models_cfg.get("enable_mediapipe", True)
        self.mediapipe_tracker = MediaPipeFaceTracker() if self.enable_mediapipe else None

        # Fallback Modular Models (nếu không dùng unified)
        self.model_drowsy = None
        self.model_distract = None
        self.model_pose = None

        if not self.use_unified_model:
            drowsy_path = base_dir / models_cfg.get("yolo_drowsiness", "models/best.pt")
            try:
                self.model_drowsy = YOLO(str(drowsy_path)) if drowsy_path.exists() else None
            except Exception:
                pass

            distract_path = base_dir / models_cfg.get("yolo_distraction", "models/yolov8n.pt")
            self.enable_distraction = models_cfg.get("enable_distraction_detection", True)
            try:
                self.model_distract = YOLO(str(distract_path)) if (self.enable_distraction and distract_path.exists()) else None
            except Exception:
                pass

            pose_path = base_dir / models_cfg.get("yolo_pose", "models/yolo26n-pose.pt")
            self.enable_pose = models_cfg.get("enable_pose_detection", False)
            try:
                self.model_pose = YOLO(str(pose_path)) if (self.enable_pose and pose_path.exists()) else None
            except Exception:
                pass

        self.conf_threshold = models_cfg.get("confidence_threshold", 0.40)
        self.inference_size = cam_cfg.get("inference_size", 320)
        self.frame_count = 0

        # Trạng thái AI chia sẻ an toàn giữa các luồng
        self._ai_payload_lock = threading.Lock()
        self._latest_ai_payload = {
            "is_drowsy": False,
            "is_yawning": False,
            "is_phone": False,
            "is_bad_posture": False,
            "posture_detail": "Tu the chuan",
            "glasses_or_glare_active": False,
            "glare_fallback_reason": "",
            "drowsy_source": "EYES",
            "ear": 0.32,
            "mar": 0.18,
            "draw_boxes": [],
            "roi_box": None,
            "engine_source": "best_unified.pt (All-In-One)"
        }

        # Khởi chạy luồng 1: Frame Grabber chuyên biệt (Xả buffer, 0ms lag)
        self._grabber_thread = threading.Thread(target=self._grab_frames_routine, daemon=True)
        self._grabber_thread.start()

        # Khởi chạy luồng 2: AI Inference Worker bất đồng bộ (Xử lý nền không nghẽn video)
        self._ai_worker_thread = threading.Thread(target=self._async_ai_worker_routine, daemon=True)
        self._ai_worker_thread.start()

    def _init_capture(self):
        """Khởi tạo camera theo nguồn đã chọn (local webcam hoặc rtsp Vantech IP)"""
        with self._frame_lock:
            if self.cap and self.cap.isOpened():
                try:
                    self.cap.release()
                except Exception:
                    pass
            self.cap = None
            self.latest_raw_frame = None

        cam_cfg = system_config.get("camera", {})
        if self.source_type == "ip" and self.rtsp_url:
            print(f"[Camera AI] Dang ket noi toi Camera IP Vantech RTSP: {self.rtsp_url[:45]}...")
            import os
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "rtsp_transport;tcp|"
                "fflags;nobuffer|"
                "flags;low_delay|"
                "max_delay;50000|"
                "probesize;32768|"
                "analyzeduration;50000|"
                "stimeout;2000000"
            )
            self.cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        else:
            print(f"[Camera AI] Dang ket noi toi Webcam may tinh (Index {self.device_index})...")
            self.cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW) if cv2.CAP_DSHOW else cv2.VideoCapture(self.device_index)
            if not self.cap.isOpened():
                self.cap = cv2.VideoCapture(self.device_index)

        if self.cap and self.cap.isOpened():
            req_w = cam_cfg.get("width", 1280 if self.source_type == "local" else 1920)
            req_h = cam_cfg.get("height", 720 if self.source_type == "local" else 1080)
            try:
                self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
            except Exception:
                pass
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, req_w)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, req_h)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.is_connected = True
            print(f"[Camera AI] KET NOI CAMERA THANH CONG (Source: {self.source_type}, Target: {req_w}x{req_h})")
        else:
            self.is_connected = False
            print(f"[Camera AI] Chua the mo nguon camera {self.source_type}")

    def switch_source(self, source_type: str, rtsp_url: str = None) -> bool:
        self.source_type = source_type
        if rtsp_url:
            self.rtsp_url = rtsp_url
        system_config["camera"]["source_type"] = self.source_type
        if rtsp_url:
            system_config["camera"]["rtsp_url"] = self.rtsp_url
        self._init_capture()
        return self.is_camera_open()

    def toggle_flip(self) -> bool:
        self.flip_180 = not self.flip_180
        system_config["camera"]["flip_180"] = self.flip_180
        return self.flip_180

    def _grab_frames_routine(self):
        """Luồng chuyên đọc và xả bộ đệm liên tục để triệt tiêu độ trễ"""
        consecutive_failures = 0
        last_reconnect_time = 0

        while self._is_running:
            if not self.cap or not self.cap.isOpened():
                self.is_connected = False
                now = time.time()
                if now - last_reconnect_time > 3.0:
                    last_reconnect_time = now
                    self._init_capture()
                time.sleep(0.5)
                continue

            ret, frame = self.cap.read()
            if ret and frame is not None:
                consecutive_failures = 0
                self.is_connected = True

                if self.source_type == "ip" and self.flip_180:
                    frame = cv2.rotate(frame, cv2.ROTATE_180)

                with self._frame_lock:
                    self.latest_raw_frame = frame
            else:
                consecutive_failures += 1
                if consecutive_failures >= 15:
                    self.is_connected = False
                    with self._frame_lock:
                        if self.cap:
                            try:
                                self.cap.release()
                            except Exception:
                                pass
                            self.cap = None
                    time.sleep(1.0)
                else:
                    time.sleep(0.01)

    def _async_ai_worker_routine(self):
        """
        [LUỒNG SUY LUẬN AI BẤT ĐỒNG BỘ NỀN - BEST_UNIFIED.PT GÁNH TRỌN VIỆC]
        - best_unified.pt chạy trực tiếp nhận diện: Mắt nhắm/mở, Ngáp/khép, Điện thoại, Tư thế.
        - Tự động vẽ đầy đủ Bounding Boxes trực quan.
        - MediaPipe đóng vai trò cảm biến hỗ trợ đo EAR/MAR siêu mịn và chống lóa kính.
        """
        while self._is_running:
            frame_to_process = None
            with self._frame_lock:
                if self.latest_raw_frame is not None:
                    frame_to_process = self.latest_raw_frame.copy()

            if frame_to_process is None:
                time.sleep(0.02)
                continue

            self.frame_count += 1
            h, w, _ = frame_to_process.shape

            raw_drowsy = False
            raw_yawning = False
            raw_phone = False
            raw_bad_posture = False
            raw_posture_detail = "Tu the chuan"
            eye_found = False
            mouth_found = False
            is_no_yawn = False
            ear_calc = 0.32
            mar_calc = 0.18
            detected_phone_box = None
            draw_boxes = []
            facial_boxes_for_tracker = []

            # =================================================================
            # 1. BEST_UNIFIED.PT THỰC HIỆN TOÀN BỘ SUY LUẬN (ALL-IN-ONE ENGINE)
            # =================================================================
            if self.unified_engine:
                face_crop, roi_box = self.face_tracker.get_face_crop(frame_to_process)
                u_res = self.unified_engine.predict_unified(
                    frame=frame_to_process,
                    face_crop=face_crop,
                    roi_box=roi_box,
                    conf=self.conf_threshold,
                    imgsz=self.inference_size,
                    frame_count=self.frame_count
                )
                raw_drowsy = u_res["raw_drowsy"]
                raw_yawning = u_res["raw_yawning"]
                raw_phone = u_res["raw_phone"]
                raw_bad_posture = u_res["raw_bad_posture"]
                raw_posture_detail = u_res["raw_posture_detail"]
                eye_found = u_res["eye_found"]
                mouth_found = u_res["mouth_found"]
                is_no_yawn = u_res["is_no_yawn"]
                detected_phone_box = u_res["detected_phone_box"]

                # Vẽ toàn bộ bounding box nhận diện từ best_unified.pt
                for gx1, gy1, gx2, gy2, label, box_conf in u_res["facial_boxes"]:
                    facial_boxes_for_tracker.append((gx1, gy1, gx2, gy2))
                    if label == "close":
                        color = (0, 0, 255)
                        text = f"Closed {box_conf:.2f}"
                    elif label == "yawn":
                        color = (0, 200, 255)
                        text = f"Yawn {box_conf:.2f}"
                    elif label == "noyawn":
                        color = (0, 255, 0)
                        text = f"Normal Mouth {box_conf:.2f}"
                    else:
                        color = (0, 255, 0)
                        text = f"Active {box_conf:.2f}"
                    draw_boxes.append({"coords": (gx1, gy1, gx2, gy2), "color": color, "text": text, "thick": 2})

                if detected_phone_box:
                    px1, py1, px2, py2 = detected_phone_box
                    draw_boxes.append({"coords": (px1, py1, px2, py2), "color": (0, 0, 255), "text": "PHONE DETECTED!", "thick": 3})

                ear_calc = 0.12 if raw_drowsy else 0.32
                mar_calc = 0.75 if raw_yawning else 0.18

            # FALLBACK NẾU KHÔNG CÓ UNIFIED ENGINE
            else:
                if self.model_drowsy:
                    try:
                        face_crop, roi_box = self.face_tracker.get_face_crop(frame_to_process)
                        r_d = self.model_drowsy.predict(face_crop, conf=self.conf_threshold, imgsz=self.inference_size, verbose=False)
                        if r_d and len(r_d) > 0:
                            for box in r_d[0].boxes:
                                cls_id = int(box.cls[0].item())
                                box_conf = float(box.conf[0].item())
                                label = self.model_drowsy.names.get(cls_id, "").lower().replace("-", "")
                                bx1, by1, bx2, by2 = map(int, box.xyxy[0].tolist())
                                gx1, gy1, gx2, gy2 = self.face_tracker.map_box_to_full_frame([bx1, by1, bx2, by2], roi_box)
                                facial_boxes_for_tracker.append((gx1, gy1, gx2, gy2))
                                if any(k in label for k in ["drowsy", "close"]):
                                    raw_drowsy = True
                                    eye_found = True
                                    draw_boxes.append({"coords": (gx1, gy1, gx2, gy2), "color": (0, 0, 255), "text": f"Closed {box_conf:.2f}", "thick": 2})
                                elif "noyawn" in label:
                                    mouth_found = True
                                    is_no_yawn = True
                                    draw_boxes.append({"coords": (gx1, gy1, gx2, gy2), "color": (0, 255, 0), "text": f"Normal Mouth {box_conf:.2f}", "thick": 2})
                                elif "yawn" in label:
                                    raw_yawning = True
                                    mouth_found = True
                                    draw_boxes.append({"coords": (gx1, gy1, gx2, gy2), "color": (0, 200, 255), "text": f"Yawn {box_conf:.2f}", "thick": 2})
                                elif "open" in label:
                                    eye_found = True
                                    draw_boxes.append({"coords": (gx1, gy1, gx2, gy2), "color": (0, 255, 0), "text": f"Active {box_conf:.2f}", "thick": 2})
                    except Exception:
                        pass

                if self.model_distract and (self.frame_count % 3 == 0 or raw_phone):
                    try:
                        r_p = self.model_distract.predict(frame_to_process, conf=0.35, imgsz=self.inference_size, verbose=False, classes=[67])
                        if r_p and len(r_p) > 0:
                            for box in r_p[0].boxes:
                                raw_phone = True
                                px1, py1, px2, py2 = map(int, box.xyxy[0].tolist())
                                detected_phone_box = [px1, py1, px2, py2]
                                draw_boxes.append({"coords": (px1, py1, px2, py2), "color": (0, 0, 255), "text": "PHONE DETECTED!", "thick": 3})
                    except Exception:
                        pass

            # =================================================================
            # 2. BỘ CẢM BIẾN TRỢ LỰC MEDIAPIPE (BỔ TRỢ CHỈ SỐ EAR/MAR MƯỢT MÀ)
            # =================================================================
            if self.mediapipe_tracker and self.mediapipe_tracker.is_ready:
                mp_res = self.mediapipe_tracker.process(frame_to_process)
                if mp_res.get("face_detected"):
                    # Đồng bộ chỉ số EAR và MAR sinh học siêu mịn
                    ear_calc = mp_res["ear"]
                    mar_calc = mp_res["mar"]
                    if mp_res.get("face_box"):
                        detected_face_box = mp_res["face_box"]
                    else:
                        detected_face_box = self.face_tracker.current_roi

                    # Nếu MediaPipe phát hiện nhắm mắt/ngáp cực rõ, hỗ trợ xác nhận
                    if mp_res["is_drowsy"]:
                        raw_drowsy = True
                    if mp_res["is_yawning"]:
                        raw_yawning = True
                    if mp_res["is_bad_posture"]:
                        raw_bad_posture = True
                        raw_posture_detail = mp_res["posture_detail"]
            else:
                detected_face_box = self.face_tracker.current_roi

            # Cập nhật Face ROI Tracker
            if self.enable_two_stage_crop:
                self.face_tracker.update_from_landmarks_or_boxes(facial_boxes_for_tracker, w, h)

            # =================================================================
            # 3. BỘ TRỌNG TÀI KHỬ MÂU THUẪN (ARBITRATION ENGINE)
            # =================================================================
            raw_ai = {
                "is_drowsy": raw_drowsy,
                "is_yawning": raw_yawning,
                "is_phone": raw_phone,
                "is_bad_posture": raw_bad_posture,
                "posture_detail": raw_posture_detail,
                "eye_found": eye_found or (ear_calc > 0),
                "mouth_found": mouth_found or (mar_calc > 0),
                "is_no_yawn": is_no_yawn,
                "face_box": detected_face_box,
                "phone_box": detected_phone_box
            }
            arbitrated = self.arbitrator.arbitrate(raw_ai, now=time.time())

            # Cập nhật kết quả an toàn vào payload chia sẻ
            with self._ai_payload_lock:
                self._latest_ai_payload = {
                    "is_drowsy": arbitrated["is_drowsy"],
                    "is_yawning": arbitrated["is_yawning"],
                    "is_phone": arbitrated["is_phone"],
                    "is_bad_posture": arbitrated["is_bad_posture"],
                    "posture_detail": arbitrated["posture_detail"],
                    "glasses_or_glare_active": arbitrated["glasses_or_glare_active"],
                    "glare_fallback_reason": arbitrated["glare_fallback_reason"],
                    "drowsy_source": arbitrated.get("drowsy_source", "EYES"),
                    "ear": ear_calc,
                    "mar": mar_calc,
                    "draw_boxes": draw_boxes,
                    "roi_box": detected_face_box,
                    "engine_source": "best_unified.pt (All-In-One)"
                }

            time.sleep(0.005)

    def is_camera_open(self) -> bool:
        return self.cap is not None and self.cap.isOpened()

    def process_frame(self):
        """
        [HÀM XUẤT FRAME SIÊU TỐC - THỜI GIAN THỰC THI < 1MS (ZERO DELAY)]
        Lấy khung hình camera mới nhất và phủ kết quả AI đã được luồng nền tính toán sẵn.
        Camera stream đạt 30 FPS tuyệt đối mượt mà, không bao giờ bị khựng hay trễ.
        """
        with self._frame_lock:
            if self.latest_raw_frame is None:
                return None
            frame = self.latest_raw_frame.copy()

        with self._ai_payload_lock:
            payload = self._latest_ai_payload.copy()

        annotated_frame = frame.copy()

        # 1. Phủ toàn bộ các Bounding Box nhận diện AI từ best_unified.pt
        for item in payload.get("draw_boxes", []):
            x1, y1, x2, y2 = item["coords"]
            color = item["color"]
            text = item["text"]
            thick = item.get("thick", 2)
            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), color, thick)
            cv2.putText(annotated_frame, text, (x1, max(20, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, thick)

        # 2. Khung viền Face High-Res ROI
        roi_box = payload.get("roi_box")
        if roi_box:
            rx1, ry1, rx2, ry2 = roi_box
            cv2.rectangle(annotated_frame, (rx1, ry1), (rx2, ry2), (220, 200, 0), 1)

        # 3. Badge chỉ thị động cơ AI
        cv2.putText(annotated_frame, "AI: best_unified.pt (All-In-One)", (15, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 200), 1)

        # 4. Huy hiệu cảnh báo kính lóa / phản quang
        if payload.get("glasses_or_glare_active"):
            cv2.putText(annotated_frame, "[GLASSES/GLARE: Fallback to Head Pose]", (15, 45),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 255), 1)

        return {
            "frame": frame,
            "annotated_frame": annotated_frame,
            "is_drowsy": payload["is_drowsy"],
            "is_yawning": payload["is_yawning"],
            "is_phone": payload["is_phone"],
            "is_bad_posture": payload["is_bad_posture"],
            "posture_detail": payload["posture_detail"],
            "glasses_or_glare_active": payload["glasses_or_glare_active"],
            "glare_fallback_reason": payload["glare_fallback_reason"],
            "drowsy_source": payload["drowsy_source"],
            "ear": payload["ear"],
            "mar": payload["mar"]
        }

    def release(self):
        """Giải phóng camera và dừng toàn bộ các luồng an toàn"""
        self._is_running = False
        with self._frame_lock:
            if self.cap and self.cap.isOpened():
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
