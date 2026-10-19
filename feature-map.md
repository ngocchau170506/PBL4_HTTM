GIAI ĐOẠN HIỆN TẠI: G0
DUYỆT LUẬT: CHƯA (đề xuất, chờ MAIN ghi "DUYỆT LUẬT")

# feature-map.md — Bản đồ tính năng & giai đoạn

Trạng thái: `TODO` | `PROPOSED` | `APPROVED` | `BUILT` | `TESTED` | `DONE`.
GATE do MAIN ghi. Chưa PASS gate thì không mở giai đoạn sau.

## Thứ tự phát triển (đã sắp xếp lại từ 11 yêu cầu gốc)
Nguyên tắc: khung & luật -> chạy được không cần AI -> thêm đầu vào thật -> thêm AI -> thêm lỗi/đánh giá -> chuyển Raspberry.

### G0 — Nền móng (chưa có code chạy)
- FEAT-001 Cấu trúc thư mục + contracts (logic.py, state.py)        BUILT (chờ MAIN duyệt kiểu)
- FEAT-002 fd_config (config.json) + nạp cấu hình                   PROPOSED (có file, chưa có bộ nạp)
- FEAT-003 Logging có trace_id + bảng ErrorCode                      TODO
- FEAT-004 Khung workbench/ (chạy từng node riêng + web_viewer kiểm tra số liệu trên PC) TODO
GATE G0: contracts được MAIN duyệt; chạy "hello pipeline" rỗng, có log + trace_id.

### G1 — Mô phỏng trên máy tính, KHÔNG AI
- FEAT-010 fd_connect: nguồn cam (webcam hoặc file video thay thế)   TODO
- FEAT-011 fd_signal: chuẩn hóa frame (timestamp, id, kích thước)    TODO
- FEAT-012 fd_flow: cây luồng tối thiểu (connect -> signal -> process(stub) -> response(log)) TODO
- FEAT-013 StubAdapter (trả kết quả giả có kiểm soát)                TODO
- FEAT-014 fd_diagnose bản đầu: bắt ErrorEvent, in nodePath          TODO
GATE G1: chạy liên tục 30 phút không rò bộ nhớ; ngắt cam giả -> báo E_CONN_LOST đúng nodePath.

### G2 — Lấy mẫu frame & Baseline
- FEAT-020 Phân luồng frame theo fps (chọn số frame cho vào xử lí)   TODO
- FEAT-021 Giai đoạn BASELINE 5–10s cho từng tài xế, cố định khung, rồi tắt/đổi tài xế TODO
- FEAT-022 Lưu/đọc baseline theo driver_id                           TODO
GATE G2: đổi tài xế -> baseline mới, không lẫn dữ liệu; test fps thấp/cao.

### G3 — AI giám sát (YOLO) cắm qua AiAdapter
- FEAT-030 YoloAdapter (cùng giao diện StubAdapter)                  TODO
- FEAT-031 Luật vi phạm (thuật toán riêng, mỗi luật 1 file)          TODO
- FEAT-032 Timeline sự kiện: -2s (tiền sự kiện) -> 0s (bắt đầu vi phạm) -> kết thúc; buffer vòng TODO
- FEAT-033 Gửi cụm frame vi phạm về server (fd_response)             TODO
GATE G3: thay StubAdapter bằng YoloAdapter KHÔNG sửa fd_flow; có số liệu đánh giá cơ bản.

### G4 — ESP32 & cảm biến
- FEAT-040 fd_connect: ESP32 (chế độ độc lập: chỉ phát dữ liệu)     TODO
- FEAT-041 ESP32 chế độ hỗ trợ (nhận tín hiệu từ máy tính)           TODO
- FEAT-042 Replay dữ liệu cảm biến liên tục để máy tính xử lí        TODO
GATE G4: mất gói/trễ gói -> báo lỗi đúng mã, không làm sập luồng cam.

### G5 — Đánh giá tổng hợp (chạy liên tục, mạng yếu)
- FEAT-050 Bộ đo: tỷ lệ phát hiện đúng, báo sai, độ trễ, FPS thực    TODO
- FEAT-051 Kịch bản mạng yếu/mất kết nối (fd_faultsim)               TODO
- FEAT-052 Báo cáo tổng hợp tự sinh                                  TODO
GATE G5: đạt ngưỡng do MAIN đặt trong fd_config (ngưỡng ghi trước, không sửa sau). Không đạt -> mục 9 "đường lùi".

### G6 — Chuyển sang Raspberry (cây phân nhánh tái cấu hình & cắt bỏ phần thừa)
- FEAT-060 Đo cấu hình thật: chạy mô phỏng vs chạy Raspberry         TODO
- FEAT-061 Tái cấu hình cây luồng cho Raspberry (bỏ workbench/web_viewer, fd_faultsim, fd_diagnose, fd_eval; chỉ giữ lõi gửi dữ liệu bất đồng bộ về máy chủ) TODO
- FEAT-062 Chạy lại toàn bộ test G1–G5 trên Raspberry                TODO
GATE G6: kết quả tương đương mô phỏng trong sai số cho phép.

## Cây luồng (plan tree)
```
fd_flow (điều phối + cache)
 ├─ fd_connect  ── camera ─┐
 │               └ esp32 ──┤
 ├─ fd_signal  <───────────┘
 ├─ fd_process
 │    ├ baseline
 │    ├ sampler (fps/frame)
 │    ├ ai (AiAdapter: stub | yolo)
 │    └ rules (mỗi luật 1 file)
 ├─ fd_response (timeline -2s/0s, gửi server)
 ├─ fd_diagnose (tìm nguyên nhân, ngoài đường chạy chính)
 ├─ fd_faultsim (mô phỏng lỗi)
 └─ fd_registry (sổ cập nhật nhánh)
```

## Sổ cập nhật nhánh (fd_registry)
Mỗi nhánh/tính năng ghi: ID, fd, phụ thuộc, phiên bản hợp đồng, test liên quan, ngày đổi cuối.
Khi một nhánh đổi, tự liệt kê nhánh kề phải chạy lại test (xem `affects` trong logic.py).

## Ngăn xếp ý tưởng mới (không làm hỏng quá trình)
Ý tưởng mới KHÔNG chèn thẳng vào giai đoạn đang chạy. Ghi vào đây:
| # | Ý tưởng | Ngày | Dự kiến giai đoạn | Trạng thái |
|---|---|---|---|---|
| I-001 | (ví dụ) cảm biến buồn ngủ qua ESP32 | | G4 | Parking |
Quy tắc: Planner xếp vào giai đoạn phù hợp -> MAIN duyệt -> mới thành FEAT-xxx.

## Change Request (đổi hợp đồng/contracts)
Tạo changes/CR-xxx.md: lý do, fd bị ảnh hưởng (`affects`), test phải chạy lại, cách lùi. Chưa MAIN duyệt thì không đụng contracts/.

## Đường lùi (mục 9 gốc)
Nếu gate không đạt sau 2 vòng sửa: dừng, Diagnoser viết báo cáo "hướng đi sai ở đâu", MAIN chọn: (a) đổi thuật toán trong fd_process, (b) đổi hợp đồng bằng CR, (c) lùi về gate trước. Không vá chồng vá.
