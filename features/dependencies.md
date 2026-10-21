# Gói phụ thuộc dự kiến theo giai đoạn (CHƯA DUYỆT, chỉ để lập kế hoạch)

`requirements.txt` hiện chỉ dùng thư viện chuẩn. Danh sách dưới đây chuyển từ `requirements.txt` cũ. Gói chỉ được thêm vào `requirements.txt` khi MAIN duyệt và giai đoạn tương ứng đã mở.

| Giai đoạn | Gói | Dùng cho | Nạp vào Raspberry |
|---|---|---|---|
| G1 | opencv-python>=4.8.0, numpy>=1.24.0 | Đọc webcam/video, chuẩn hóa frame | Có |
| G1 (workbench) | flask>=3.0.0, flask-socketio>=5.3.6 | `workbench/web_viewer` trên PC | Không |
| G3 | ultralytics>=8.0.0, torch>=2.0.0, torchvision | YoloAdapter trên PC | Chưa quyết, cân nhắc NCNN/ONNX cho Pi |
| G3 | mediapipe>=0.10.0 | Điểm mốc khuôn mặt (`models/face_landmarker.task`) | Chưa quyết |
| G4 | pyserial>=3.5 | Đọc ESP32 qua serial | Có |
| G3 trở đi | requests>=2.31.0 | Gửi dữ liệu về máy chủ (`fd_response`) | Có |

Ghi chú: gói của `workbench/` không bao giờ nạp vào Raspberry (xem `workbench/workbench.md` mục 5).
