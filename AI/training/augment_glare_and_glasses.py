import os
import cv2
import random
import argparse
import numpy as np
from pathlib import Path
from typing import Tuple, List

class GlareAndGlassesAugmentor:
    """
    [BỘ SINH DỮ LIỆU ĐẶC THÙ: CHỐNG LÓA KÍNH MẮT, PHẢN QUANG & CABIN ĐÊM]
    Tạo ra các trường hợp khó trong môi trường lái xe thực tế để train file .pt:
    1. Vệt sáng chóa đèn xe đối diện (Specular Glare & Flare trên tròng kính).
    2. Kính râm / Tròng kính tối màu phản quang (Tinted Glasses Simulation).
    3. Cabin xe tối ban đêm / Camera hồng ngoại (IR Cabin Low-Light).
    4. Rung lắc xe (Motion Blur & Gaussian Noise).
    """
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.img_out = self.output_dir / "images"
        self.lbl_out = self.output_dir / "labels"
        self.img_out.mkdir(parents=True, exist_ok=True)
        self.lbl_out.mkdir(parents=True, exist_ok=True)

    def add_synthetic_glare(self, image: np.ndarray, eye_boxes: List[Tuple[float, float, float, float]] = None) -> np.ndarray:
        """Tạo vệt sáng trắng chóa phản quang trên tròng kính / vùng mắt"""
        aug_img = image.copy()
        h, w, _ = aug_img.shape

        # Nếu có tọa độ mắt thì chiếu glare vào mắt, nếu không thì chiếu ngẫu nhiên nửa trên mặt
        if eye_boxes:
            box = random.choice(eye_boxes)
            bx, by, bw, bh = box
            cx, cy = int(bx * w), int(by * h)
            radius = int(max(bh * h * 0.8, 12))
        else:
            cx = random.randint(int(w * 0.3), int(w * 0.7))
            cy = random.randint(int(h * 0.2), int(h * 0.5))
            radius = random.randint(15, 35)

        # Tạo mask quầng sáng xuyên thấu (Gradient Specular Spot)
        overlay = np.zeros_like(aug_img, dtype=np.uint8)
        color = (random.randint(230, 255), random.randint(230, 255), random.randint(230, 255))
        cv2.circle(overlay, (cx, cy), radius, color, -1)
        overlay = cv2.GaussianBlur(overlay, (radius * 2 + 1, radius * 2 + 1), radius // 2)

        alpha = random.uniform(0.55, 0.85)
        aug_img = cv2.addWeighted(aug_img, 1.0, overlay, alpha, 0)
        return aug_img

    def add_sunglasses_tint(self, image: np.ndarray, eye_boxes: List[Tuple[float, float, float, float]] = None) -> np.ndarray:
        """Mô phỏng tròng kính râm hoặc gọng kính cận dày màu tối"""
        aug_img = image.copy()
        h, w, _ = aug_img.shape

        if eye_boxes:
            for box in eye_boxes:
                bx, by, bw, bh = box
                x1 = max(0, int((bx - bw / 2) * w) - 4)
                y1 = max(0, int((by - bh / 2) * h) - 4)
                x2 = min(w, int((bx + bw / 2) * w) + 4)
                y2 = min(h, int((by + bh / 2) * h) + 4)

                roi = aug_img[y1:y2, x1:x2]
                if roi.size > 0:
                    dark_roi = cv2.multiply(roi, np.array([0.35, 0.35, 0.35]))
                    aug_img[y1:y2, x1:x2] = dark_roi.astype(np.uint8)
                    cv2.rectangle(aug_img, (x1, y1), (x2, y2), (20, 20, 20), 2)
        else:
            # Làm tối nửa trên khuôn mặt
            y1, y2 = int(h * 0.25), int(h * 0.45)
            x1, x2 = int(w * 0.25), int(w * 0.75)
            roi = aug_img[y1:y2, x1:x2]
            if roi.size > 0:
                aug_img[y1:y2, x1:x2] = cv2.multiply(roi, np.array([0.4, 0.4, 0.4])).astype(np.uint8)

        return aug_img

    def simulate_night_ir_cabin(self, image: np.ndarray) -> np.ndarray:
        """Mô phỏng cabin xe tối mịt, quay bằng camera hồng ngoại (IR Monochrome)"""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Tăng tương phản nhẹ như IR LED
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        ir = clahe.apply(gray)
        # Thêm nhiễu hạt ISO cao
        noise = np.random.normal(0, 8, ir.shape).astype(np.int16)
        ir_noisy = np.clip(ir.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        return cv2.cvtColor(ir_noisy, cv2.COLOR_GRAY2BGR)

    def add_motion_blur(self, image: np.ndarray) -> np.ndarray:
        """Mô phỏng xe rung lắc làm mờ khung hình"""
        size = random.choice([5, 7, 9])
        kernel = np.zeros((size, size))
        kernel[int((size - 1) / 2), :] = np.ones(size)
        kernel = kernel / size
        return cv2.filter2D(image, -1, kernel)

    def process_dataset(self, source_img_dir: Path, source_lbl_dir: Path, multiplier: int = 2):
        """Duyệt qua tập dữ liệu gốc và sinh dữ liệu tăng cường"""
        img_paths = list(source_img_dir.glob("*.jpg")) + list(source_img_dir.glob("*.png"))
        print(f"[Augmentor] Tìm thấy {len(img_paths)} ảnh gốc trong {source_img_dir}")

        count_generated = 0
        for img_p in img_paths:
            img = cv2.imread(str(img_p))
            if img is None:
                continue

            lbl_p = source_lbl_dir / f"{img_p.stem}.txt"
            lbl_content = ""
            eye_boxes = []

            if lbl_p.exists():
                with open(lbl_p, "r", encoding="utf-8") as f:
                    lbl_content = f.read()
                # Trích xuất các box của class 1 (eye_open) và 2 (eye_close)
                for line in lbl_content.strip().split("\n"):
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        cls_id = int(parts[0])
                        if cls_id in [1, 2]:  # Mắt
                            eye_boxes.append((float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])))

            # 1. Sinh biến thể Glare (Lóa kính)
            img_glare = self.add_synthetic_glare(img, eye_boxes)
            out_name_glare = f"{img_p.stem}_aug_glare"
            cv2.imwrite(str(self.img_out / f"{out_name_glare}.jpg"), img_glare)
            if lbl_content:
                with open(self.lbl_out / f"{out_name_glare}.txt", "w", encoding="utf-8") as f:
                    f.write(lbl_content)
            count_generated += 1

            # 2. Sinh biến thể IR Night Cabin (Đêm tối)
            img_night = self.simulate_night_ir_cabin(img)
            out_name_night = f"{img_p.stem}_aug_night"
            cv2.imwrite(str(self.img_out / f"{out_name_night}.jpg"), img_night)
            if lbl_content:
                with open(self.lbl_out / f"{out_name_night}.txt", "w", encoding="utf-8") as f:
                    f.write(lbl_content)
            count_generated += 1

            # 3. Sinh biến thể Sunglasses (Kính râm / gọng tối)
            if eye_boxes:
                img_glasses = self.add_sunglasses_tint(img, eye_boxes)
                out_name_glass = f"{img_p.stem}_aug_glasses"
                cv2.imwrite(str(self.img_out / f"{out_name_glass}.jpg"), img_glasses)
                if lbl_content:
                    with open(self.lbl_out / f"{out_name_glass}.txt", "w", encoding="utf-8") as f:
                        f.write(lbl_content)
                count_generated += 1

        print(f"[Augmentor] ✅ Hoàn tất! Đã sinh {count_generated} ảnh biến thể tại: {self.output_dir}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tăng cường dữ liệu chống lóa kính mắt & đêm tối")
    parser.add_argument("--img_dir", type=str, default="dataset/images/train", help="Thư mục ảnh gốc")
    parser.add_argument("--lbl_dir", type=str, default="dataset/labels/train", help="Thư mục nhãn gốc")
    parser.add_argument("--out_dir", type=str, default="dataset_augmented", help="Thư mục lưu kết quả")
    args = parser.parse_args()

    aug = GlareAndGlassesAugmentor(output_dir=Path(args.out_dir))
    if Path(args.img_dir).exists():
        aug.process_dataset(Path(args.img_dir), Path(args.lbl_dir))
    else:
        print(f"[Notice] Thư mục '{args.img_dir}' chưa tồn tại. Sẵn sàng chạy khi bạn nạp dataset!")
