# CLAUDE.md — Luật bắt buộc cho mọi AI làm việc trong dự án

Dự án: Hệ thống giám sát tài xế (Camera + ESP32 + Raspberry; mô phỏng trên máy tính trước).
Ngôn ngữ: Python. Chỉ dùng thư viện chuẩn cho tới khi MAIN duyệt dependency.
"MAIN" = chủ dự án (con người). Chỉ MAIN được: ghi GATE, duyệt CR, sửa comment_allowlist.txt, duyệt dependency.

Trạng thái tài liệu này: ĐỀ XUẤT. Có hiệu lực khi MAIN ghi "DUYỆT LUẬT" vào feature-map.md.

## 0. Thứ tự đọc (bắt buộc trước khi làm bất cứ việc gì)
1. CLAUDE.md (file này)
2. agents.md (bạn đang đóng vai nào? Không có `ROLE:` thì chỉ được đọc và hỏi)
3. feature-map.md (giai đoạn hiện tại, dòng `GIAI ĐOẠN HIỆN TẠI`)
4. scope/<giai đoạn>.json (danh sách file được phép đụng)
5. features/component.txt (hợp đồng từng component)
6. src/contracts/logic.py, src/contracts/state.py (kiểu dữ liệu, luật luồng)
Không đọc xong = không được sửa code.

## 1. Quy trình 1 việc (KHÔNG ĐƯỢC làm 1 lần hết)
PROPOSE -> MAIN DUYỆT -> BUILD -> TEST -> REPORT -> MAIN DUYỆT -> MERGE
- PROPOSE: nêu việc, file sẽ đụng (phải nằm trong scope), rủi ro, cách test. Chưa viết code.
- BUILD: chỉ trong phạm vi đã duyệt. Mỗi lượt tối đa 1 component.
- TEST: chạy test độc lập ở workbench/ (không chỉ nhìn Terminal).
- REPORT: dùng mẫu ở mục 8. Không báo "xong" nếu chưa có kết quả thật của `python workbench/gate.py`.
- Chuyển giai đoạn chỉ khi MAIN ghi "GATE Gx: PASS" vào feature-map.md.

## 2. Luật kiến trúc
- R1. Mỗi file 1 vai trò. Thuật toán KHÔNG chung file với xử lí ảnh/tín hiệu/kết nối/phản hồi.
- R2. Các fd chỉ nói chuyện qua fd_flow. Cấm import chéo giữa fd (trừ fd_flow được nối các fd). Kiểm bằng check_scope.py [S2].
- R3. Dữ liệu giữa fd chỉ đi bằng kiểu trong src/contracts/. Đổi kiểu = Change Request.
- R4. Mọi cấu hình (địa chỉ cam, fps, ngưỡng, timeout...) nằm trong src/fd_config/config.json, không hard-code.
- R5. Mọi gói tin có trace_id; mọi lỗi có mã lỗi (ErrorCode) + node_path.
- R6. AI/model (YOLO...) chỉ cắm qua giao diện AiAdapter. Phải chạy được bằng StubAdapter trước.
- R7. Phần nặng không chạy nổi trên Raspberry: để ở lớp "offload", không làm ô nhiễm lõi.

## 3. Luật sửa lỗi và chống đi đường vòng
- F1. Cấm sửa lỗi dựa riêng vào output Terminal. Phải tái hiện lỗi ở workbench/ rồi tìm nguyên nhân.
- F2. Mỗi bug có 1 hồ sơ: changes/BUG-xxx.md (dùng changes/BUG-TEMPLATE.md).
- F3. Cấm "sửa kèm": 1 bug = 1 thay đổi nhỏ nhất. Thấy lỗi khác -> ghi BUG mới, KHÔNG sửa luôn.
- F4. Cấm nuốt lỗi (except rỗng, bỏ warning, hạ log level để im). Warning phải được xử lí hoặc ghi rõ lí do chấp nhận.
- F5. Sửa xong phải chạy lại: test của node đó + test tích hợp của node kề (theo cây luồng).
- F6. Không đổi hợp đồng để "cho test qua". Test sai thì báo MAIN.
- F7. Cấm dùng comment để đi đường vòng: comment tắt kiểm tra (`# noqa`, `type: ignore`, `pragma: no cover`), comment giữ chỗ (`TODO`, `FIXME`, `...`, "làm sau"), code bị comment lại để né lỗi, hàm/khối chỉ có comment hoặc `pass`, `except` rỗng. Chưa làm xong thì báo BLOCKED, không để code giả. Comment giải thích "vì sao" được phép.
- F8. `python workbench/gate.py` phải SẠCH trước mọi REPORT. Ngoại lệ comment chỉ MAIN ghi vào workbench/comment_allowlist.txt. AI không được sửa file đó, không được sửa script kiểm để né.

## 4. Cấm tuyệt đối
- Viết lại/refactor lớn khi chưa có Change Request được duyệt.
- Đụng file ngoài scope/<giai đoạn>.json.
- Tự thêm dependency (kể cả sửa requirements.txt) khi chưa có MAIN duyệt.
- Xóa test, hạ ngưỡng đánh giá, sửa số liệu để "đạt".
- Chạy lệnh Terminal ảnh hưởng hệ thống khi chưa được MAIN cho phép (mục 6).
- Sửa scope/*.json, feature-map.md (trừ mục kế hoạch), hoặc src/contracts/ khi chưa có CR.

## 5. Khi thiếu thông tin
Dừng, hỏi MAIN bằng tối đa 3 câu hỏi cụ thể. Không tự đoán rồi làm tiếp.

## 6. Lệnh Terminal
- Được phép tự do: `python workbench/gate.py`, `python workbench/check_scope.py <Gx>`, `python workbench/check_comments.py <đường dẫn>`, `python -m unittest discover -s workbench/tests`, và mọi lệnh chỉ đọc.
- Cần MAIN cho phép: cài package, mở camera/cổng serial/ESP32, ghi đè file cấu hình, xóa file, chạy dài hơn 60s, mở mạng, `git` ghi vào repo.

## 7. Ghi nhận thay đổi
Mỗi lần merge: cập nhật feature-map.md (trạng thái) + 1 dòng vào changes/LOG.md (ngày, ID, fd, tóm tắt, ai duyệt).

## 8. Mẫu REPORT (bắt buộc)
```
ID: <FEAT/BUG/CR-xxx>      Giai đoạn: Gx
Đã làm: ...
File đã đụng: ... (phải khớp scope)
Test đã chạy: <tên test> -> <kết quả thật>
Cổng: python workbench/gate.py -> <SẠCH / CÓ VI PHẠM + nội dung>
Chưa kiểm chứng / giới hạn: ...
Rủi ro còn lại: ...
Đề xuất bước tiếp (chờ MAIN duyệt): ...
```

## 9. Nhiều công cụ AI
Luật này áp dụng cho Claude Code, Antigravity, Gemini, GPT và mọi công cụ khác.
- Công cụ tự đọc file này (Claude Code): làm theo như trên.
- Công cụ không tự đọc: MAIN dán nguyên mục 0, 1, 3, 4 và dòng `ROLE:` vào đầu phiên.
- Chốt chặn thật không nằm ở lời hứa của AI mà ở `python workbench/gate.py` và phạm vi trong scope/*.json. Mọi công cụ đều phải chạy được cổng này.
