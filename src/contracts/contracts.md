# [C-TYPE] GIAO THỨC KIỂU DỮ LIỆU

types.py # Khai báo kiểu gói: RawPacket, SignalPacket, DetectionResult, ViolationEvent
errors.py # Bảng mã lỗi ErrorCode chuẩn (E*CONN*_, E*SIG*_, E*FLOW*_, E*PROC*_)
state_machine.py # Máy trạng thái: BOOT -> IDLE -> BASELINE -> MONITOR -> DEGRADED -> FAULT
