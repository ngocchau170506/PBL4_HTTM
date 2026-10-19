# [C-PROCESS] LUẬT & TÍNH TOÁN MỨC RỦI RO

# [C-PROCESS] LUẬT & TÍNH TOÁN MỨC RỦI RO

## 1. Giao diện và Luật cơ bản

`eye_drowsiness.py`

: Luật phát hiện nhắm mắt / ngủ gật (thresholds & sliding window)

`mouth_yawn.py`

: Luật phát hiện ngáp liên tục (count thresholds)

`head_pose.py`

: Luật phát hiện quay đầu / gục đầu (angle thresholds + baseline)

`phone_distraction.py`

: Luật phát hiện cầm / nghe điện thoại (object detection + tracking)

## 2. Tính toán Rủi ro

`violation_analyzer.py`

: Tổng hợp kết quả luật -> DetectionResult -> tích lũy violation_score

`safety_meter.py`

: Chuyển violation_score thành mức độ rủi ro (RISK_LOW / MEDIUM / HIGH / CRITICAL) và định tuyến node tiếp theo
