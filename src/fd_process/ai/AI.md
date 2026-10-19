# [C-PROCESS] XỬ LÝ NHẬN DẠNG (AI)

adapter_base.py # Giao diện trừu tượng AiAdapter (load, infer, health)
stub_adapter.py # Adapter giả lập kết quả chuẩn (chạy kiểm thử không cần model nặng)
yolo_adapter.py # Adapter bọc model YOLO/NCNN/ONNX (chỉ cắm vào khi G0-G2 đã PASS)
