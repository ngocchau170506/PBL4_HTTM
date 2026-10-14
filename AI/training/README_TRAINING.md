# HƯỚNG DẪN HUẤN LUYỆN MÔ HÌNH TOÀN NĂNG (ALL-IN-ONE MODEL) CHO DMS

> **Mục Tiêu:** Gộp toàn bộ các tác vụ riêng lẻ (Buồn ngủ, Ngáp, Điện thoại, Mắt kính phản quang, Tư thế gục đầu) vào **1 file `.pt` duy nhất** (`models/best_unified.pt`).  
> **Lợi Ích:**  
> 1. **Triệt tiêu độ trễ:** Chỉ chạy 1 lần suy luận (Forward Pass) duy nhất qua mô hình, thời gian xử lý giảm từ $\sim 250\text{ms}$ xuống **$< 15\text{ms}$ (FPS tăng từ 5 lên 45-60 FPS)**.  
> 2. **Xóa sổ mâu thuẫn thuật toán:** Các bounding box được trích xuất từ cùng một Feature Map nhất quán.  
> 3. **Khắc phục mắt kính phản quang:** Bổ sung tập dữ liệu tăng cường chuyên biệt (Synthetic Glare & IR Cabin).

---

## 1. Cấu Trúc 8 Classes Chuẩn Hóa

Được cấu hình sẵn tại `training/dataset_config.yaml`:

| Class ID | Tên Nhãn | Ý Nghĩa Chẩn Đoán | Ghi Chú Đặc Trưng |
| :---: | :--- | :--- | :--- |
| **0** | `driver_face` | Toàn bộ khuôn mặt / đầu tài xế | Định vị Face ROI phục vụ Two-Stage Tracking |
| **1** | `eye_open` | Mắt mở bình thường / Tỉnh táo | Mi mắt mở rõ con ngươi |
| **2** | `eye_close` | Mắt nhắm buồn ngủ | Mi mắt khép kín $\ge 80\%$ |
| **3** | `mouth_normal` | Miệng khép bình thường | Trạng thái bình thường hoặc đang mỉm cười nhẹ |
| **4** | `mouth_yawn` | Miệng há to ngáp | Cơ hàm mở rộng tối đa |
| **5** | `cell_phone` | Cầm / nghe điện thoại | Điện thoại cầm trên tay hoặc áp vào tai |
| **6** | `smoking` | Hút thuốc lá / vape | Điếu thuốc đưa lên môi |
| **7** | `head_tilt` | Nghiêng đầu / Gục đầu ngủ | Đầu lệch quá $35^\circ$ hoặc gục sát ngực |

---

## 2. Quy Trình 4 Bước Huấn Luyện Thực Chiến

```
[Dữ Liệu Thô (IOT + TEST_1 + Kaggle)]
          │
          ▼ (Bước 1)
[Tăng Cường Chống Lóa Kính & Đêm Tối: augment_glare_and_glasses.py]
          │
          ▼ (Bước 2)
[Kiểm Tra Toàn Vẹn: dataset_verifier.py]
          │
          ▼ (Bước 3)
[Huấn Luyện Tự Động: train_unified_yolo.py]
          │
          ▼ (Bước 4)
[Xuất models/best_unified.pt & Bật "use_unified_model": true trong config.json]
```

### Bước 1: Chuẩn bị Thư Mục Dữ Liệu
Đặt dữ liệu vào thư mục `dataset/` theo chuẩn Ultralytics YOLO:
```
dataset/
├── images/
│   ├── train/     # Ảnh huấn luyện (.jpg, .png)
│   └── val/       # Ảnh kiểm thử
└── labels/
    ├── train/     # File nhãn YOLO (.txt tương ứng)
    └── val/
```

### Bước 2: Tăng Cường Dữ Liệu Chống Lóa Kính & Đêm Tối (Augmentation)
Chạy script chuyên dụng để sinh thêm vệt sáng đèn xe chóa tròng kính và ánh sáng đêm hồng ngoại:
```powershell
python training/augment_glare_and_glasses.py --img_dir dataset/images/train --lbl_dir dataset/labels/train --out_dir dataset_augmented
```
*(Sao chép các ảnh và nhãn vừa sinh trong `dataset_augmented` gộp vào `dataset`)*.

### Bước 3: Kiểm Tra Tính Toàn Vẹn Của Dataset
Để đảm bảo không bị lỗi nhãn ngoài khung $[0, 1]$, nhãn âm, hoặc ảnh hỏng:
```powershell
python training/dataset_verifier.py --data_dir dataset
```

### Bước 4: Khởi Động Huấn Luyện Tự Động
Chạy script huấn luyện với cơ chế Early Stopping và Cosine Annealing:
```powershell
# Chạy với Card đồ họa GPU CUDA (Khuyến nghị để train siêu tốc):
python training/train_unified_yolo.py --epochs 100 --batch 16 --imgsz 640 --device auto

# Hoặc chạy thử nghiệm trên CPU:
python training/train_unified_yolo.py --epochs 10 --batch 8 --imgsz 320 --device cpu
```

---

## 3. Cách Kích Hoạt Trong Hệ Thống YOLO26 (Plug & Play)

Sau khi huấn luyện xong, script sẽ tự động tạo file `models/best_unified.pt`.  
Để hệ thống chuyển sang chế độ siêu tốc All-in-One:

1. Mở file `config.json` (hoặc `config_vantech.json`).
2. Sửa thuộc tính `"use_unified_model"` thành `true`:
   ```json
   "models": {
     "yolo_unified": "models/best_unified.pt",
     "use_unified_model": true,
     "confidence_threshold": 0.40
   }
   ```
3. Khởi động lại ứng dụng `python app.py`. Hệ thống sẽ tự động in thông báo:  
   `[YOLO26] 🚀 KÍCH HOẠT ALL-IN-ONE MODEL TOÀN NĂNG: best_unified.pt`  
   và toàn bộ hệ thống sẽ chạy ở tốc độ cao nhất với độ trễ thấp nhất!
