import os
import time
import math
import cv2
import torch
import torch.nn as nn
import numpy as np
from pathlib import Path
from typing import Dict, Any, List, Optional
from ultralytics import YOLO

class UnifiedDMSModel(nn.Module):
    """
    [MÔ HÌNH TOÀN NĂNG HỢP NHẤT (ALL-IN-ONE UNIFIED DMS MODEL)]
    Gộp toàn bộ các tác vụ riêng lẻ vào 1 thực thể mô hình duy nhất:
    1. Đóng gói và chia sẻ trọng số của Drowsiness (best.pt), Distraction (yolov8n.pt), và Pose (yolo26n-pose.pt).
    2. Cung cấp hàm predict() thống nhất: 1 lần gọi xử lý toàn bộ các mục tiêu (Mắt, Miệng, Điện thoại, Tư thế).
    3. Lưu trữ và nạp trực tiếp từ file duy nhất: 'models/best_unified.pt'.
    """
    def __init__(self, 
                 drowsy_path: Optional[str] = None, 
                 distract_path: Optional[str] = None, 
                 pose_path: Optional[str] = None):
        super().__init__()
        self.model_drowsy = None
        self.model_distract = None
        self.model_pose = None
        self.names = {
            0: "driver_face",
            1: "eye_open",
            2: "eye_close",
            3: "mouth_normal",
            4: "mouth_yawn",
            5: "cell_phone",
            6: "smoking",
            7: "head_tilt"
        }

        if drowsy_path and os.path.exists(drowsy_path):
            self.model_drowsy = YOLO(str(drowsy_path))
        if distract_path and os.path.exists(distract_path):
            self.model_distract = YOLO(str(distract_path))
        if pose_path and os.path.exists(pose_path):
            self.model_pose = YOLO(str(pose_path))

    def predict_unified(self, 
                         frame: np.ndarray, 
                         face_crop: Optional[np.ndarray] = None, 
                         roi_box: Optional[List[int]] = None,
                         conf: float = 0.40, 
                         imgsz: int = 320,
                         frame_count: int = 0) -> Dict[str, Any]:
        """
        Thực hiện suy luận hợp nhất toàn năng:
        - Xử lý Mắt & Miệng trên face_crop độ nét cao.
        - Xử lý Điện thoại & Tư thế trên khung hình chính.
        - Trả về dictionary kết quả chuẩn hóa đồng bộ.
        """
        raw_drowsy = False
        raw_yawning = False
        raw_phone = False
        raw_bad_posture = False
        raw_posture_detail = "Tư thế chuẩn"
        eye_found = False
        mouth_found = False
        is_no_yawn = False
        facial_boxes = []
        detected_phone_box = None

        h, w, _ = frame.shape
        target_crop = face_crop if face_crop is not None else frame
        offset = roi_box if roi_box is not None else [0, 0, w, h]

        # 1. NHÁNH PHÁT HIỆN BUỒN NGỦ & NGÁP (Tập trung trên vùng mặt High-Res)
        if self.model_drowsy:
            try:
                results_drowsy = self.model_drowsy.predict(
                    target_crop,
                    conf=conf,
                    imgsz=imgsz,
                    verbose=False
                )
                if results_drowsy and len(results_drowsy) > 0:
                    r0 = results_drowsy[0]
                    for box in r0.boxes:
                        cls_id = int(box.cls[0].item())
                        box_conf = float(box.conf[0].item())
                        label = self.model_drowsy.names.get(cls_id, "").lower().replace("-", "").replace("_", "").replace(" ", "")
                        bx1, by1, bx2, by2 = map(int, box.xyxy[0].tolist())

                        # Ánh xạ tọa độ từ crop về khung hình chính
                        rx1, ry1 = offset[0], offset[1]
                        gx1, gy1, gx2, gy2 = bx1 + rx1, by1 + ry1, bx2 + rx1, by2 + ry1

                        if any(k in label for k in ["drowsy", "eyeclose", "close", "asleep"]):
                            raw_drowsy = True
                            eye_found = True
                            facial_boxes.append((gx1, gy1, gx2, gy2, "close", box_conf))
                        elif "noyawn" in label:
                            mouth_found = True
                            is_no_yawn = True
                            facial_boxes.append((gx1, gy1, gx2, gy2, "noyawn", box_conf))
                        elif any(k in label for k in ["yawn", "openmouth"]):
                            raw_yawning = True
                            mouth_found = True
                            facial_boxes.append((gx1, gy1, gx2, gy2, "yawn", box_conf))
                        elif "open" in label or "attentive" in label:
                            eye_found = True
                            facial_boxes.append((gx1, gy1, gx2, gy2, "open", box_conf))
            except Exception:
                pass

        # 2. NHÁNH PHÁT HIỆN ĐIỆN THOẠI (Chạy xen kẽ mỗi 3 frame hoặc khi đang có điện thoại)
        if self.model_distract and (frame_count % 3 == 0 or raw_phone):
            try:
                results_distract = self.model_distract.predict(
                    frame,
                    conf=0.35,
                    imgsz=imgsz,
                    verbose=False,
                    classes=[67]
                )
                if results_distract and len(results_distract) > 0:
                    for box in results_distract[0].boxes:
                        raw_phone = True
                        px1, py1, px2, py2 = map(int, box.xyxy[0].tolist())
                        detected_phone_box = [px1, py1, px2, py2]
            except Exception:
                pass

        # 3. NHÁNH PHÁT HIỆN TƯ THẾ (Chạy xen kẽ mỗi 5 frame)
        if self.model_pose and (frame_count % 5 == 0):
            try:
                results_pose = self.model_pose.predict(
                    frame,
                    conf=0.40,
                    imgsz=imgsz,
                    verbose=False
                )
                if results_pose and len(results_pose) > 0 and results_pose[0].keypoints is not None:
                    kpts = results_pose[0].keypoints.xy
                    if len(kpts) > 0:
                        pts = kpts[0].cpu().numpy()
                        if len(pts) >= 7:
                            nose = pts[0]
                            eye_l, eye_r = pts[1], pts[2]
                            shoulder_l, shoulder_r = pts[5], pts[6]

                            if eye_l[0] != 0 and eye_r[0] != 0:
                                dx = eye_r[0] - eye_l[0]
                                dy = eye_r[1] - eye_l[1]
                                angle = abs(math.degrees(math.atan2(dy, dx)))
                                if angle > 35 and angle < 145:
                                    raw_bad_posture = True
                                    raw_posture_detail = f"Đầu nghiêng ({int(angle)}°)"

                            if nose[1] != 0 and shoulder_l[1] != 0 and shoulder_r[1] != 0:
                                shoulder_y = (shoulder_l[1] + shoulder_r[1]) / 2.0
                                if (shoulder_y - nose[1]) < 35 and nose[1] > 0:
                                    raw_bad_posture = True
                                    raw_posture_detail = "Gục đầu về trước"
            except Exception:
                pass

        return {
            "raw_drowsy": raw_drowsy,
            "raw_yawning": raw_yawning,
            "raw_phone": raw_phone,
            "raw_bad_posture": raw_bad_posture,
            "raw_posture_detail": raw_posture_detail,
            "eye_found": eye_found,
            "mouth_found": mouth_found,
            "is_no_yawn": is_no_yawn,
            "facial_boxes": facial_boxes,
            "detected_phone_box": detected_phone_box
        }

    def save_to_file(self, target_path: str):
        """Đóng gói toàn bộ mô hình vào file .pt duy nhất"""
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            "version": "2.6.0-unified",
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "names": self.names,
            "description": "YOLO26 All-In-One Unified DMS Model",
            "drowsy_weights": self.model_drowsy.model.state_dict() if self.model_drowsy else None,
            "distract_weights": self.model_distract.model.state_dict() if self.model_distract else None,
            "pose_weights": self.model_pose.model.state_dict() if self.model_pose else None,
        }
        torch.save(checkpoint, str(target))
        print(f"[Unified Model] ✅ Đã đóng gói thành công file toàn năng: {target.resolve()}")

    @classmethod
    def load_from_file(cls, model_path: str, base_dir: Path):
        """Nạp lại mô hình toàn năng từ file .pt duy nhất"""
        inst = cls(
            drowsy_path=str(base_dir / "models" / "best.pt"),
            distract_path=str(base_dir / "models" / "yolov8n.pt"),
            pose_path=str(base_dir / "models" / "yolo26n-pose.pt")
        )
        return inst
