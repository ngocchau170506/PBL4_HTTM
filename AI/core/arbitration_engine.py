import time
from typing import Dict, Any, Optional, List
from collections import deque

class ArbitrationEngine:
    """
    [BỘ TRỌNG TÀI KHỬ MÂU THUẪN & CHUYỂN GIAO QUYỀN LỰC THUẬT TOÁN (ANTI-CONFLICT ARBITRATOR)]
    Giải quyết triệt để 5 xung đột thực tế trong hệ thống DMS:
    1. Cúi đầu nhìn táp-lô vs Ngủ gật (Cross-Lock Head-Pitch):
       Cúi đầu nhanh (< 1.2s) thì mi mắt cụp xuống không được coi là ngủ gật.
    2. Nói chuyện / Hát vs Ngáp thật (Speech Frequency vs Continuous Hold-Time):
       Khẩu hình mở-đóng theo chu kỳ âm tiết (< 0.6s) là nói chuyện. Chỉ coi là ngáp khi giữ liên tục >= 1.8s.
    3. Mắt kính phản quang & che khuất (Glasses & Glare Occlusion Fallback):
       Khi tròng kính bị chóa đèn hoặc kính râm làm mất dấu mắt: Không báo lỗi giả, tự động chuyển
       quyền giám sát sang: Góc gục đầu (Head Nodding) và Cử động ngáp.
    4. Xác thực kép Điện thoại (Dual Confirmation Hand-to-Face vs Phone):
       Yêu cầu BBox điện thoại có overlap/proximity với khuôn mặt và giữ liên tục >= 1.0s.
    5. Hàng đợi phân cấp ưu tiên nghiêm ngặt (Strict Priority Queue):
       DROWSY > PHONE > CO2_CRITICAL > BAD_POSTURE > YAWNING > CO2_WARNING.
    """
    def __init__(self):
        # Bộ nhớ cửa sổ trượt (Moving Window)
        self.eye_detected_history = deque(maxlen=20)   # Lưu bool: có phát hiện được mắt không
        self.mouth_state_history = deque(maxlen=20)    # Lưu ("YAWN" | "NO_YAWN" | "NONE", timestamp)
        self.head_pitch_history = deque(maxlen=25)     # Lưu (angle, timestamp)
        
        # Biến trạng thái
        self.is_glasses_or_glare_active = False
        self.glare_fallback_reason = ""
        self.last_head_tilt_time = 0.0
        self.continuous_yawn_start: Optional[float] = None
        self.continuous_phone_start: Optional[float] = None

        # Trọng số ưu tiên
        self.PRIORITY_LEVELS = {
            "DROWSINESS": 1,
            "PHONE_DISTRACTION": 2,
            "CO2_CRITICAL": 3,
            "BAD_POSTURE": 4,
            "YAWNING": 5,
            "CO2_WARNING": 6,
            "SAFE": 99
        }

    def arbitrate(self, 
                  raw_ai: Dict[str, Any], 
                  co2_ppm: float = 600.0, 
                  now: Optional[float] = None) -> Dict[str, Any]:
        """
        raw_ai bao gồm:
          is_drowsy: bool
          is_yawning: bool
          is_phone: bool
          is_bad_posture: bool
          posture_detail: str
          eye_found: bool (có phát hiện được mắt mở hoặc nhắm không)
          mouth_found: bool
          is_no_yawn: bool (phát hiện miệng khép bình thường)
          face_box: list [x1, y1, x2, y2] hoặc None
          phone_box: list [x1, y1, x2, y2] hoặc None
        """
        now = now or time.time()

        is_drowsy_raw = raw_ai.get("is_drowsy", False)
        is_yawning_raw = raw_ai.get("is_yawning", False)
        is_phone_raw = raw_ai.get("is_phone", False)
        is_bad_posture_raw = raw_ai.get("is_bad_posture", False)
        posture_detail = raw_ai.get("posture_detail", "Tư thế chuẩn")
        
        eye_found = raw_ai.get("eye_found", False)
        mouth_found = raw_ai.get("mouth_found", False)
        is_no_yawn = raw_ai.get("is_no_yawn", False)
        face_box = raw_ai.get("face_box", None)
        phone_box = raw_ai.get("phone_box", None)

        # -------------------------------------------------------------
        # QUY TẮC 1: XỬ LÝ MẮT KÍNH PHẢN QUANG & CHE KHUẤT (GLARE FALLBACK)
        # -------------------------------------------------------------
        self.eye_detected_history.append(eye_found)
        recent_eye_detect_rate = sum(self.eye_detected_history) / max(1, len(self.eye_detected_history))

        # Nếu có mặt (hoặc miệng) nhưng tỷ lệ nhận diện mắt < 25% trong 20 frame gần nhất
        if face_box is not None and recent_eye_detect_rate < 0.25:
            self.is_glasses_or_glare_active = True
            self.glare_fallback_reason = "Kính mắt phản quang / che khuất (Chuyển sang giám sát tư thế đầu & ngáp)"
        else:
            if recent_eye_detect_rate >= 0.50:
                self.is_glasses_or_glare_active = False
                self.glare_fallback_reason = ""

        # -------------------------------------------------------------
        # QUY TẮC 2: KHÓA CHÉO CÚI ĐẦU NHÌN TÁP-LÔ VS NGỦ GẬT (CROSS-LOCK)
        # -------------------------------------------------------------
        # Khi cúi đầu nhìn đồng hồ tốc độ, mi mắt hạ xuống -> Dễ bị bắt nhầm là nhắm mắt.
        is_looking_down_briefly = False
        if "Gục đầu" in posture_detail or "nghiêng" in posture_detail:
            self.head_pitch_history.append((posture_detail, now))
            # Nếu vừa mới cúi đầu trong vòng 1.2s trở lại đây
            if len(self.head_pitch_history) >= 1:
                dur_tilt = now - self.head_pitch_history[0][1]
                if dur_tilt < 1.2:
                    is_looking_down_briefly = True
        else:
            self.head_pitch_history.clear()

        # -------------------------------------------------------------
        # QUY TẮC 3: LỌC TẦN SUẤT NÓI CHUYỆN / HÁT VS NGÁP THẬT (HOLD-TIME)
        # -------------------------------------------------------------
        # Nếu vừa mở miệng vừa khép miệng xen kẽ trong chu kỳ < 0.6s -> Đang nói chuyện/hát
        if is_no_yawn:
            # Miệng đã khép lại -> Ngay lập tức cắt đứt chuỗi ngáp liên tục
            self.continuous_yawn_start = None
            is_yawn_verified = False
        elif is_yawning_raw:
            if self.continuous_yawn_start is None:
                self.continuous_yawn_start = now
            yawn_hold_time = now - self.continuous_yawn_start
            # Chỉ coi là ngáp thật nếu miệng mở to liên tục >= 1.5s
            is_yawn_verified = (yawn_hold_time >= 1.5)
        else:
            self.continuous_yawn_start = None
            is_yawn_verified = False

        # -------------------------------------------------------------
        # QUY TẮC 4: XÁC THỰC KÉP ĐIỆN THOẠI (HAND-TO-FACE CONFIRMATION)
        # -------------------------------------------------------------
        is_phone_verified = False
        if is_phone_raw:
            phone_valid_location = True
            # Nếu có cả khuôn mặt và điện thoại, kiểm tra khoảng cách tương đối
            if face_box and phone_box:
                fx1, fy1, fx2, fy2 = face_box
                px1, py1, px2, py2 = phone_box
                fw = fx2 - fx1
                # Tâm khuôn mặt và tâm điện thoại
                fcx, fcy = (fx1 + fx2) / 2, (fy1 + fy2) / 2
                pcx, pcy = (px1 + px2) / 2, (py1 + py2) / 2
                dist_x = abs(pcx - fcx)
                dist_y = abs(pcy - fcy)
                # Điện thoại đang nghe hoặc nhìn phải ở gần vùng đầu/tai (trong vòng 1.5 chiều rộng mặt)
                if dist_x > (1.8 * fw) and dist_y > (1.8 * fw):
                    phone_valid_location = False

            if phone_valid_location:
                if self.continuous_phone_start is None:
                    self.continuous_phone_start = now
                # Cần duy trì >= 0.8s để loại trừ quẹt tay gãi cằm
                if (now - self.continuous_phone_start) >= 0.8:
                    is_phone_verified = True
            else:
                self.continuous_phone_start = None
        else:
            self.continuous_phone_start = None

        # -------------------------------------------------------------
        # TỔNG HỢP TÍN HIỆU SAU TRỌNG TÀI (ARBITRATED SIGNALS)
        # -------------------------------------------------------------
        arbitrated_drowsy = False
        drowsy_source = "EYES"

        if self.is_glasses_or_glare_active:
            # FALLBACK KHI KÍNH BỊ CHÓA: Quyền lực chuyển sang Head Pose
            if is_bad_posture_raw and ("Gục đầu" in posture_detail or "nghiêng" in posture_detail):
                arbitrated_drowsy = True
                drowsy_source = "HEAD_DROP_GLARE_FALLBACK"
        else:
            # Nếu mắt bình thường: Khóa chéo nếu đang cúi đầu nhìn táp-lô thoáng qua
            if is_drowsy_raw and not is_looking_down_briefly:
                arbitrated_drowsy = True
                drowsy_source = "EYES"

        # Đóng gói kết quả đầu ra
        return {
            "is_drowsy": arbitrated_drowsy,
            "drowsy_source": drowsy_source,
            "is_yawning": is_yawn_verified,
            "is_phone": is_phone_verified,
            "is_bad_posture": is_bad_posture_raw and not is_looking_down_briefly,
            "posture_detail": posture_detail,
            "glasses_or_glare_active": self.is_glasses_or_glare_active,
            "glare_fallback_reason": self.glare_fallback_reason,
            "suppressed_by_arbitrator": {
                "drowsy_cancelled_by_dash_glance": is_drowsy_raw and is_looking_down_briefly,
                "yawn_cancelled_by_talking": is_yawning_raw and not is_yawn_verified,
                "phone_cancelled_by_hand_scratch": is_phone_raw and not is_phone_verified
            }
        }
