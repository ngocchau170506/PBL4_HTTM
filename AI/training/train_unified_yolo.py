import os
import shutil
import argparse
import torch
from pathlib import Path
from ultralytics import YOLO

def train_unified_model(
    config_yaml: str = "training/dataset_config.yaml",
    base_model: str = "models/yolov8n.pt",
    epochs: int = 100,
    batch_size: int = 16,
    img_size: int = 640,
    device: str = "auto"
):
    """
    [SCRIPT HUẤN LUYỆN TOÀN NĂNG: GỘP THÀNH 1 FILE .PT DUY NHẤT]
    Tập trung toàn bộ 8 classes vào 1 mô hình suy luận duy nhất (< 15ms):
    - Tự động nhận diện Card đồ họa CUDA GPU hoặc fallback CPU.
    - Áp dụng Early Stopping (patience=15) và Cosine Annealing LR.
    - Tự động xuất và đóng gói file 'models/best_unified.pt'.
    """
    base_dir = Path(__file__).resolve().parent.parent
    yaml_path = base_dir / config_yaml
    model_weight_path = base_dir / base_model

    # Tự động nhận diện thiết bị phần cứng
    if device == "auto":
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            target_device = 0
            print(f"[Hardware AI] 🚀 Phát hiện GPU CUDA: {gpu_name}")
        else:
            target_device = "cpu"
            print("[Hardware AI] 💻 Không có GPU CUDA, sẽ huấn luyện trên CPU đa luồng.")
    else:
        target_device = device

    print("=" * 70)
    print("🎯 BẮT ĐẦU HUẤN LUYỆN ALL-IN-ONE MODEL TOÀN NĂNG (YOLO26)")
    print(f"• Dataset Config: {yaml_path}")
    print(f"• Base Pretrained: {model_weight_path}")
    print(f"• Epochs: {epochs} | Batch: {batch_size} | Imgsz: {img_size} | Device: {target_device}")
    print("=" * 70)

    # Nạp base model
    if model_weight_path.exists():
        model = YOLO(str(model_weight_path))
    else:
        print(f"[Notice] Không thấy {model_weight_path.name}, tải 'yolov8n.pt' mặc định...")
        model = YOLO("yolov8n.pt")

    # Khởi động quá trình huấn luyện
    results = model.train(
        data=str(yaml_path),
        epochs=epochs,
        batch=batch_size,
        imgsz=img_size,
        device=target_device,
        patience=15,          # Dừng sớm nếu 15 epochs không cải thiện loss
        save=True,
        save_period=10,
        val=True,
        optimizer="auto",
        lr0=0.01,
        cos_lr=True,          # Cosine Learning Rate Scheduler
        mosaic=0.5,           # Data Augmentation chống phụ thuộc góc
        flipud=0.0,           # Không lật dọc vì tài xế ngồi theo chiều thẳng
        fliplr=0.5,           # Lật ngang 50% để mô phỏng lái xe tay lái nghịch
        project=str(base_dir / "runs" / "detect"),
        name="unified_dms_train",
        exist_ok=True
    )

    # Đóng gói xuất ra file models/best_unified.pt
    best_trained = base_dir / "runs" / "detect" / "unified_dms_train" / "weights" / "best.pt"
    dest_unified = base_dir / "models" / "best_unified.pt"

    if best_trained.exists():
        dest_unified.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(best_trained, dest_unified)
        print("\n" + "=" * 70)
        print(f"🎉 HUẤN LUYỆN THÀNH CÔNG RỰC RỠ!")
        print(f"✅ Đã sao chép file model toàn năng vào: {dest_unified}")
        print("💡 Để kích hoạt ngay trong hệ thống:")
        print("   Mở 'config.json' và đổi: \"use_unified_model\": true")
        print("=" * 70)
    else:
        print("[Warning] Không tìm thấy file weights xuất ra tại:", best_trained)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Huấn luyện mô hình toàn năng All-in-One cho DMS")
    parser.add_argument("--config", type=str, default="training/dataset_config.yaml", help="File dataset YAML")
    parser.add_argument("--base", type=str, default="models/yolov8n.pt", help="File trọng số pretrained")
    parser.add_argument("--epochs", type=int, default=100, help="Số epochs")
    parser.add_argument("--batch", type=int, default=16, help="Batch size")
    parser.add_argument("--imgsz", type=int, default=640, help="Kích thước ảnh")
    parser.add_argument("--device", type=str, default="auto", help="cuda hoặc cpu hoặc auto")
    args = parser.parse_args()

    train_unified_model(
        config_yaml=args.config,
        base_model=args.base,
        epochs=args.epochs,
        batch_size=args.batch,
        img_size=args.imgsz,
        device=args.device
    )
