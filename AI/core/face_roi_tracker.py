import numpy as np
import cv2
from typing import Tuple, Optional, List, Dict, Any

class FaceRoiTracker:
    """
    [KIẾN TRÚC TWO-STAGE DYNAMIC HIGH-RES FACE ROI TRACKER]
    - Giải quyết triệt để vấn đề: Khi tài xế ngồi xa camera (0.8m - 2.0m),
      ảnh 1080p/720p co xuống 320p khiến mắt chỉ còn 4-8 pixel -> Mất nhận diện hoàn toàn.
    - Cơ chế:
      1. Tầng 1: Tự động định vị & theo dõi hộp bao (Bounding Box) Khuôn mặt/Đầu của tài xế
         trên khung hình gốc ở độ phân giải cao nhất.
      2. Tầng 2: Cắt vùng Dynamic High-Res ROI (có padding an toàn 35%) đưa vào model nhận diện
         mắt & miệng. Mắt trong ROI crop có kích thước 40-80 pixel (tăng độ nét 500%).
      3. Áp dụng Exponential Moving Average (EMA) để giữ khung ROI mượt mà, không rung lắc.
      4. Tự động quy đổi tọa độ bounding box từ ROI crop về tọa độ khung hình gốc.
    """
    def __init__(self, ema_alpha: float = 0.25, padding_ratio: float = 0.35):
        self.ema_alpha = ema_alpha
        self.padding_ratio = padding_ratio
        self.current_roi: Optional[List[int]] = None  # [x1, y1, x2, y2]
        self.frames_without_face = 0
        self.max_miss_frames = 30  # Sau 30 frame không thấy mặt, mở rộng dần vùng quét
        self.has_active_face = False

    def get_initial_driver_anchor(self, frame_w: int, frame_h: int) -> List[int]:
        """Vùng neo mặc định của vị trí tài xế (65% trung tâm phía trên khung hình)"""
        x1 = int(frame_w * 0.18)
        x2 = int(frame_w * 0.82)
        y1 = int(frame_h * 0.05)
        y2 = int(frame_h * 0.85)
        return [x1, y1, x2, y2]

    def update_from_landmarks_or_boxes(self, 
                                       facial_points_or_boxes: List[Tuple[int, int, int, int]], 
                                       frame_w: int, 
                                       frame_h: int) -> List[int]:
        """
        Cập nhật vị trí ROI dựa trên tọa độ mắt, mũi, miệng hoặc pose keypoints.
        facial_points_or_boxes: Danh sách các hộp (x1, y1, x2, y2) của các thành phần mặt.
        """
        if not facial_points_or_boxes:
            self.frames_without_face += 1
            if self.frames_without_face > self.max_miss_frames or self.current_roi is None:
                # Mở rộng dần về Driver Anchor
                anchor = self.get_initial_driver_anchor(frame_w, frame_h)
                if self.current_roi is None:
                    self.current_roi = anchor
                else:
                    # Mở rộng dần 5% mỗi frame
                    self.current_roi[0] = max(anchor[0], int(self.current_roi[0] * 0.95))
                    self.current_roi[1] = max(anchor[1], int(self.current_roi[1] * 0.95))
                    self.current_roi[2] = min(anchor[2], int(self.current_roi[2] * 1.05))
                    self.current_roi[3] = min(anchor[3], int(self.current_roi[3] * 1.05))
                self.has_active_face = False
            return self.current_roi

        self.frames_without_face = 0
        self.has_active_face = True

        # Tìm hộp bao nhỏ nhất chứa toàn bộ các đặc trưng khuôn mặt
        min_x = min(b[0] for b in facial_points_or_boxes)
        min_y = min(b[1] for b in facial_points_or_boxes)
        max_x = max(b[2] for b in facial_points_or_boxes)
        max_y = max(b[3] for b in facial_points_or_boxes)

        bw = max_x - min_x
        bh = max_y - min_y

        # Thêm padding an toàn để bao trọn cả trán, cằm, tai và tóc
        pad_x = int(max(bw * self.padding_ratio, 30))
        pad_y = int(max(bh * (self.padding_ratio + 0.15), 40))

        target_x1 = max(0, min_x - pad_x)
        target_y1 = max(0, min_y - pad_y)
        target_x2 = min(frame_w, max_x + pad_x)
        target_y2 = min(frame_h, max_y + pad_y)

        target_roi = [target_x1, target_y1, target_x2, target_y2]

        if self.current_roi is None:
            self.current_roi = target_roi
        else:
            # EMA Smoothing để chống rung lắc khung hình
            a = self.ema_alpha
            self.current_roi = [
                int(a * target_roi[0] + (1 - a) * self.current_roi[0]),
                int(a * target_roi[1] + (1 - a) * self.current_roi[1]),
                int(a * target_roi[2] + (1 - a) * self.current_roi[2]),
                int(a * target_roi[3] + (1 - a) * self.current_roi[3]),
            ]

        # Đảm bảo kích thước tối thiểu cho ROI (ít nhất 160x160)
        roi_w = self.current_roi[2] - self.current_roi[0]
        roi_h = self.current_roi[3] - self.current_roi[1]
        if roi_w < 160 or roi_h < 160:
            cx = (self.current_roi[0] + self.current_roi[2]) // 2
            cy = (self.current_roi[1] + self.current_roi[3]) // 2
            half = max(80, max(roi_w, roi_h) // 2)
            self.current_roi = [
                max(0, cx - half),
                max(0, cy - half),
                min(frame_w, cx + half),
                min(frame_h, cy + half)
            ]

        return self.current_roi

    def get_face_crop(self, frame: np.ndarray) -> Tuple[np.ndarray, List[int]]:
        """
        Cắt ảnh khuôn mặt ở độ phân giải gốc của camera.
        Trả về (crop_image, [x1, y1, x2, y2])
        """
        fh, fw, _ = frame.shape
        if self.current_roi is None:
            self.current_roi = self.get_initial_driver_anchor(fw, fh)

        x1, y1, x2, y2 = self.current_roi
        x1 = max(0, min(x1, fw - 10))
        y1 = max(0, min(y1, fh - 10))
        x2 = max(x1 + 10, min(x2, fw))
        y2 = max(y1 + 10, min(y2, fh))

        crop = frame[y1:y2, x1:x2].copy()
        return crop, [x1, y1, x2, y2]

    @staticmethod
    def map_box_to_full_frame(box_xyxy: List[int], roi_offset: List[int]) -> List[int]:
        """Chuyển đổi tọa độ từ ROI crop về tọa độ toàn khung hình gốc"""
        rx1, ry1, _, _ = roi_offset
        return [
            box_xyxy[0] + rx1,
            box_xyxy[1] + ry1,
            box_xyxy[2] + rx1,
            box_xyxy[3] + ry1
        ]
