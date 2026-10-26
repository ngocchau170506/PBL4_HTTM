# EXP-004 — Nhãn dán thông tin & Hướng dẫn chạy kiểm thử toàn diện trên máy cho MAIN

## 1. Thông tin định danh (Label)
- **Mã thí nghiệm**: `EXP-004`
- **Nguồn thuật toán chuẩn**: Trích xuất từ `E:\AI\XUAT\PBL4_HTTM-AI\AI` (Hybrid Two-Stage Verification: YOLO + MediaPipe FaceLandmarker on-demand, EAR/MAR Z-Score cá nhân hóa, 3D Head Pose EMA).
- **Trạng thái Brain (`src/contracts/logic.py` & `state.py`)**: **KHÔNG THAY ĐỔI (100% NGUYÊN VẸN)**.
- **Vị trí cô lập**: `workbench/experiments/EXP-004/` (không import ngược vào `src/`, không làm loạn cấu trúc dự án).

## 2. Cấu trúc các file trong thư mục này
| File | Vai trò |
|---|---|
| `README.md` | Nhãn dán thông tin và hướng dẫn chạy nhanh cho MAIN. |
| `config_exp004.json` | Bản sao cấu hình thử nghiệm Camera 60 FPS, ngưỡng EAR/MAR/Z-Score và ESP32 115200 baud. |
| `mock_camera_60fps.py` | Bộ phát và bắt khung hình 60 FPS (`16.67ms/frame`), đo jitter và lưu ảnh mẫu `.pgm` vào `captured_frames/`. |
| `hybrid_ai_adapter.py` | Bộ thích ứng AI 2 tầng (`HybridTwoStageAdapter`) cắm chuẩn vào `FlowEngine` qua giao diện `AiAdapter`. |
| `run_full_roles_bench.py` | Script chạy toàn diện báo cáo đồng bộ các vai trò: `BRAIN`, `CAM`, `ESP`, `AI`, `RULES`, `RESP`, `G5-CALC`. |
| `benchmark_60fps_report.json` | Kết quả số đo thực tế sinh ra sau khi chạy. |
| `captured_frames/` | Thư mục chứa các khung hình mẫu bắt được ở các trạng thái `NORMAL`, `DROWSY`, `CONFLICT`, `YAWNING`. |

## 3. Lệnh chạy trực tiếp cho MAIN trên máy
Từ thư mục gốc `E:\AI\AI`, chạy lệnh:
```powershell
python workbench/experiments/EXP-004/run_full_roles_bench.py
```
