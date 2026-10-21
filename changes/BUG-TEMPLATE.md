# BUG-xxx — <tên ngắn>

- Ngày phát hiện:
- Giai đoạn: Gx
- Người ghi (vai): Diagnoser | Tester | Experimenter
- Trạng thái: MỞ | ĐANG SỬA | ĐÃ SỬA | CHƯA_SUA_DUOC

## Triệu chứng
Điều thấy được, kèm số liệu thật (không chỉ copy Terminal).

## nodePath và ErrorCode
- node_path: (node hoặc cạnh lỗi, ví dụ `signal->process.sampler`)
- ErrorCode: (mã hiện có; nếu cần mã mới thì tạo CR)
- Phân loại: nguồn | dữ liệu | hợp đồng | thuật toán | hiệu năng | cấu hình | quy trình

## Cách tái hiện ở workbench
Lệnh hoặc kịch bản chạy được lại (F1: không sửa lỗi chỉ dựa vào Terminal).

## Nguyên nhân gốc
Một đoạn ngắn. Nếu chưa rõ: ghi "chưa rõ" và những gì đã loại trừ.

## Thay đổi nhỏ nhất đề xuất (F3)
File và dòng cần đổi. Lỗi khác phát hiện thêm ghi BUG mới, không sửa kèm.

## Test phải chạy lại (F5)
Test của node đó + test tích hợp node kề.

## Kết quả sau sửa
Kết quả thật của `python workbench/gate.py` và test liên quan. Nếu bản sửa gây vấn đề mới: ghi rõ và đặt trạng thái FIX_CO_VAN_DE.
