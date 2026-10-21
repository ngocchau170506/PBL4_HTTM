# Hệ thống cảnh báo an toàn cho tài xế (PBL4_HTTM)

Camera + ESP32 + Raspberry Pi 4 Model B giám sát tài xế (nhắm mắt, ngáp, quay đầu, dùng điện thoại, khí CO2 trong cabin). Mô phỏng trên máy tính trước (`mode="sim"`), sau đó chuyển sang Raspberry (`mode="rasp"`).

Trạng thái: giai đoạn **G2** (Lấy mẫu frame, Baseline & Kết nối cảm biến ESP32 G2.5). Luật làm việc đã được MAIN duyệt ngày 2026-10-20.

## Nếu bạn là AI: đọc theo thứ tự này, không đọc xong thì không sửa code

1. `CLAUDE.md`: luật bắt buộc (quy trình PROPOSE -> BUILD -> TEST -> REPORT, luật kiến trúc R1-R7, luật sửa lỗi F1-F8, thí nghiệm hiệu năng ở mục 10).
2. `agents.md`: các vai và quyền hạn. Không có dòng `ROLE:` từ MAIN thì chỉ được đọc và hỏi.
3. `feature-map.md`: giai đoạn hiện tại, danh sách FEAT, cây luồng.
4. `scope/<giai đoạn>.json`: danh sách file được phép đụng.
5. `features/component.txt`: hợp đồng từng component.
6. `src/contracts/logic.py`, `src/contracts/state.py`: kiểu dữ liệu và máy trạng thái.

README này không phải nguồn luật. Khi mâu thuẫn, `CLAUDE.md` thắng.

## Cấu trúc thư mục

| Thư mục / file              | Vai trò                                                                                                                                |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `src/contracts/`            | Hợp đồng dữ liệu (chỉ đổi qua Change Request)                                                                                          |
| `src/fd_*`                  | Các khối chức năng: connect, signal, process, flow, response, config, diagnose, faultsim, eval, registry. Chỉ nói chuyện qua `fd_flow` |
| `src/fd_config/config.json` | Cấu hình duy nhất                                                                                                                      |
| `workbench/`                | Cổng kiểm (`gate.py`), phòng thí nghiệm, test. Chỉ chạy trên PC, không nạp vào Raspberry                                               |
| `scope/`                    | Phạm vi file được phép của từng giai đoạn                                                                                              |
| `changes/`                  | Hồ sơ BUG, CR, EXP (thí nghiệm) và `LOG.md`                                                                                            |
| `features/`                 | Hợp đồng component, gói phụ thuộc dự kiến                                                                                              |
| `models/`                   | Trọng số AI (`best.pt`, `face_landmarker.task`)                                                                                        |
| `DRAFT/`                    | Bản nháp cũ, không dùng (kể cả README của dự án YOLO26 cũ)                                                                             |

## Lệnh kiểm tra (an toàn, chạy tự do)

```
python workbench/gate.py
python workbench/check_scope.py G2
python workbench/check_comments.py src
python -m unittest discover -s workbench/tests
```

`gate.py` phải SẠCH trước mọi báo cáo.

## Giai đoạn

G0 nền móng, G1 mô phỏng không AI, G2 lấy mẫu frame và baseline, G3 AI (YOLO) qua `AiAdapter`, G4 ESP32 và cảm biến, G5 đánh giá tổng hợp, G6 chuyển sang Raspberry. Chi tiết ở `feature-map.md`.
