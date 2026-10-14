import sys
from pathlib import Path

# Thêm thư mục gốc vào PYTHONPATH
base_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(base_dir))

from core.unified_model import UnifiedDMSModel

def main():
    print("=" * 65)
    print("📦 BẮT ĐẦU ĐÓNG GÓI 3 FILE .PT THÀNH 1 FILE TOÀN NĂNG DUY NHẤT")
    print("=" * 65)

    drowsy_p = base_dir / "models" / "best.pt"
    distract_p = base_dir / "models" / "yolov8n.pt"
    pose_p = base_dir / "models" / "yolo26n-pose.pt"
    dest_unified = base_dir / "models" / "best_unified.pt"

    print(f"• Nạp Trọng Số Mắt & Miệng: {drowsy_p.name}")
    print(f"• Nạp Trọng Số Điện Thoại:  {distract_p.name}")
    print(f"• Nạp Trọng Số Tư Thế Pose: {pose_p.name}")

    unified_model = UnifiedDMSModel(
        drowsy_path=str(drowsy_p),
        distract_path=str(distract_p),
        pose_path=str(pose_p)
    )

    unified_model.save_to_file(str(dest_unified))

    file_size_mb = dest_unified.stat().st_size / (1024 * 1024)
    print("=" * 65)
    print(f"🎉 HOÀN TẤT! File toàn năng 'best_unified.pt' ({file_size_mb:.2f} MB) đã sẵn sàng!")
    print(f"Vị trí: {dest_unified}")
    print("=" * 65)

if __name__ == "__main__":
    main()
