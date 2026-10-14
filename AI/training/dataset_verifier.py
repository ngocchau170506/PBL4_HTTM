import os
import cv2
import argparse
from pathlib import Path
from collections import Counter

CLASS_NAMES = {
    0: "driver_face",
    1: "eye_open",
    2: "eye_close",
    3: "mouth_normal",
    4: "mouth_yawn",
    5: "cell_phone",
    6: "smoking",
    7: "head_tilt"
}

def verify_dataset(data_dir: Path):
    """
    [CÔNG CỤ KIỂM TRA TOÀN VẸN DATASET TRƯỚC KHI TRAIN]
    1. Kiểm tra ảnh hỏng, nhãn thiếu hoặc nhãn không có ảnh.
    2. Kiểm tra tọa độ chuẩn hóa [0.0, 1.0].
    3. Thống kê phân bố 8 classes để phát hiện mất cân bằng dữ liệu.
    """
    data_dir = Path(data_dir)
    img_dir = data_dir / "images"
    lbl_dir = data_dir / "labels"

    print(f"\n" + "="*60)
    print(f"🔍 ĐANG KIỂM TRA DATASET TẠI: {data_dir.resolve()}")
    print("="*60)

    if not img_dir.exists():
        print(f"❌ Không tìm thấy thư mục ảnh: {img_dir}")
        return False

    splits = ["train", "val", "test"]
    total_imgs = 0
    total_boxes = 0
    global_class_counts = Counter()
    issues = []

    for split in splits:
        split_img_dir = img_dir / split
        split_lbl_dir = lbl_dir / split

        if not split_img_dir.exists():
            continue

        images = list(split_img_dir.glob("*.jpg")) + list(split_img_dir.glob("*.png")) + list(split_img_dir.glob("*.jpeg"))
        print(f"\n📂 Tập [{split.upper()}]: Tìm thấy {len(images)} ảnh.")
        total_imgs += len(images)

        for img_p in images:
            # 1. Kiểm tra đọc ảnh
            im = cv2.imread(str(img_p))
            if im is None:
                issues.append(f"Ảnh hỏng không thể mở: {img_p}")
                continue

            lbl_p = split_lbl_dir / f"{img_p.stem}.txt"
            if not lbl_p.exists():
                issues.append(f"Ảnh thiếu file nhãn: {img_p.name} (không có {lbl_p.name})")
                continue

            with open(lbl_p, "r", encoding="utf-8") as f:
                lines = f.readlines()

            for line_no, line in enumerate(lines, 1):
                parts = line.strip().split()
                if not parts:
                    continue
                if len(parts) < 5:
                    issues.append(f"Dòng nhãn thiếu trường (cần 5 phần tử): {lbl_p.name}:{line_no}")
                    continue

                try:
                    cls_id = int(parts[0])
                    x, y, w, h = map(float, parts[1:5])
                except ValueError:
                    issues.append(f"Lỗi cú pháp số học: {lbl_p.name}:{line_no}")
                    continue

                if cls_id not in CLASS_NAMES:
                    issues.append(f"Class ID không hợp lệ ({cls_id}): {lbl_p.name}:{line_no}")
                    continue

                if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 0.0 <= w <= 1.0 and 0.0 <= h <= 1.0):
                    issues.append(f"Tọa độ vượt ngưỡng [0, 1] ({x:.2f}, {y:.2f}, {w:.2f}, {h:.2f}): {lbl_p.name}:{line_no}")

                global_class_counts[cls_id] += 1
                total_boxes += 1

    print("\n" + "-"*60)
    print("📊 BẢNG THỐNG KÊ PHÂN BỐ CÁC CLASS:")
    print("-"*60)
    for cid in range(8):
        cname = CLASS_NAMES.get(cid, "unknown")
        count = global_class_counts.get(cid, 0)
        pct = (count / max(1, total_boxes)) * 100
        print(f"  Class {cid:1d} [{cname:<14}]: {count:>6d} hộp ({pct:5.1f}%)")

    print("-"*60)
    print(f"Tổng số ảnh kiểm tra: {total_imgs}")
    print(f"Tổng số Bounding Boxes: {total_boxes}")

    if issues:
        print(f"\n⚠️ PHÁT HIỆN {len(issues)} VẤN ĐỀ TRONG DATASET:")
        for iss in issues[:10]:
            print(f"  - {iss}")
        if len(issues) > 10:
            print(f"  ... và còn {len(issues) - 10} vấn đề khác.")
        return False
    else:
        print("\n✅ DATASET HOÀN TOÀN HỢP LỆ VÀ SẴN SÀNG ĐỂ HUẤN LUYỆN!")
        return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kiểm tra tính toàn vẹn của dataset YOLO")
    parser.add_argument("--data_dir", type=str, default="dataset", help="Thư mục dataset chứa images/ và labels/")
    args = parser.parse_args()

    verify_dataset(Path(args.data_dir))
