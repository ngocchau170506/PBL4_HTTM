"""Giao thức đóng khung dữ liệu hai chiều tốc độ cao FDFRAME qua UART (FEAT-110 / G11).

- Định dạng khung nhị phân chuẩn hóa cho kênh truyền Pi <-> ESP32 với baudrate 921,600 bps.
- Cấu trúc khung:
    [Header: 2 bytes (0xAA, 0x55)]
    [Frame Type: 1 byte]
    [Sequence: 2 bytes (uint16, big-endian)]
    [Payload Length: 2 bytes (uint16, big-endian)]
    [Payload: N bytes]
    [CRC16: 2 bytes (CRC-16-CCITT, big-endian)]
- Hỗ trợ đa dạng loại bản tin: Telemetry, Sự kiện vi phạm, Phân mảnh ảnh JPEG (Chunk),
  Biên nhận Downlink ACK, Trạng thái kết nối mạng WiFi/Server.
- Tính toán toàn vẹn dữ liệu bằng giải thuật CRC16-CCITT chuẩn, chỉ dùng thư viện chuẩn Python (Luật R4/R2).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import struct
import time
from typing import Any, Dict, Optional, Tuple, Union

# Tiền tố nhận diện khung (Magic bytes)
MAGIC_HEADER = b"\xAA\x55"
HEADER_LEN = 7  # 2 bytes magic + 1 byte type + 2 bytes seq + 2 bytes len
CRC_LEN = 2
MIN_FRAME_LEN = HEADER_LEN + CRC_LEN  # 9 bytes khi payload rỗng

# Bảng mã loại khung tin (Frame Types)
FRAME_TYPE_HEARTBEAT = 0x00       # Nhịp tim kiểm tra đường truyền
FRAME_TYPE_TELEMETRY = 0x01       # Dữ liệu cảm biến tổng hợp (GPS, MQ135, Pi health)
FRAME_TYPE_VIOLATION = 0x02       # Thông báo sự kiện vi phạm (buồn ngủ, ngáp)
FRAME_TYPE_IMAGE_CHUNK = 0x03     # Mảnh dữ liệu ảnh JPEG từ FrameRingBuffer
FRAME_TYPE_DOWNLINK_ACK = 0x04    # Biên nhận từ Server gửi ngược về (kèm tọa độ XYZ)
FRAME_TYPE_COMMAND = 0x05         # Lệnh điều khiển xuống ngoại vi (còi, loa, reset)
FRAME_TYPE_NETWORK_STATUS = 0x06  # Báo cáo trạng thái WiFi/Server của ESP32


def _generate_crc_table() -> Tuple[int, ...]:
    table = []
    for byte in range(256):
        cur = byte << 8
        for _ in range(8):
            if cur & 0x8000:
                cur = ((cur << 1) ^ 0x1021) & 0xFFFF
            else:
                cur = (cur << 1) & 0xFFFF
        table.append(cur)
    return tuple(table)


_CRC16_TABLE: Tuple[int, ...] = _generate_crc_table()


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    """Tính toán mã kiểm tra toàn vẹn CRC-16-CCITT (đa thức 0x1021) tối ưu bảng tra cứu O(N)."""
    crc = initial
    table = _CRC16_TABLE
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ table[(crc >> 8) ^ byte]
    return crc


@dataclass(frozen=True)
class FdFrame:
    """Cấu trúc dữ liệu khung tin UART tốc độ cao."""

    frame_type: int
    seq: int
    payload: bytes
    crc: int
    ts_ms: int

    @property
    def payload_length(self) -> int:
        return len(self.payload)

    def to_json(self) -> Optional[Dict[str, Any]]:
        """Giải mã payload thành JSON dictionary nếu payload là định dạng chuỗi văn bản."""
        if not self.payload:
            return None
        try:
            parsed = json.loads(self.payload.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else None
        except (UnicodeDecodeError, ValueError):
            return None


def encode_fd_frame(
    frame_type: int,
    payload: Union[bytes, str, Dict[str, Any]],
    seq: int = 0,
) -> bytes:
    """Đóng gói dữ liệu thành chuỗi byte tuân thủ cấu trúc FDFRAME kèm mã kiểm tra CRC16."""
    if isinstance(payload, dict):
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    elif isinstance(payload, str):
        payload_bytes = payload.encode("utf-8")
    else:
        payload_bytes = bytes(payload)

    payload_len = len(payload_bytes)
    if payload_len > 65535:
        raise ValueError(f"Payload vượt quá giới hạn 65535 bytes: {payload_len}")

    safe_seq = int(seq) & 0xFFFF
    safe_type = int(frame_type) & 0xFF

    header = MAGIC_HEADER + struct.pack(">BHH", safe_type, safe_seq, payload_len)
    data_to_crc = header[len(MAGIC_HEADER):] + payload_bytes
    crc = crc16_ccitt(data_to_crc)
    
    return header + payload_bytes + struct.pack(">H", crc)


def decode_fd_frame(buffer: bytes) -> Tuple[Optional[FdFrame], int, Optional[str]]:
    """Phân tích chuỗi byte nhận được từ UART và trích xuất FdFrame.

    Trả về: (khung_tin, số_byte_đã_tiêu_thụ, thông_báo_lỗi)
    - Nếu buffer chưa đủ độ dài: trả về (None, 0, None) để tiếp tục tích lũy.
    - Nếu lỗi checksum/cú pháp: bỏ qua vị trí lỗi và dịch tiếp con trỏ tìm header mới.
    """
    if len(buffer) < MIN_FRAME_LEN:
        return None, 0, None

    # Tìm vị trí bắt đầu của Magic Header
    header_idx = buffer.find(MAGIC_HEADER)
    if header_idx < 0:
        # Toàn bộ buffer không chứa magic header -> loại bỏ dữ liệu rác
        return None, len(buffer), "Không tìm thấy Magic Header"

    if header_idx > 0:
        # Bỏ qua phần rác đệm phía trước header
        buffer = buffer[header_idx:]

    if len(buffer) < MIN_FRAME_LEN:
        return None, header_idx, None

    # Đọc thông tin trường Header
    frame_type, seq, payload_len = struct.unpack(">BHH", buffer[2:7])
    total_frame_len = HEADER_LEN + payload_len + CRC_LEN

    if len(buffer) < total_frame_len:
        # Chưa nhận đủ toàn bộ khung tin -> giữ nguyên chờ đọc tiếp
        return None, header_idx, None

    payload = buffer[HEADER_LEN : HEADER_LEN + payload_len]
    expected_crc = struct.unpack(">H", buffer[HEADER_LEN + payload_len : total_frame_len])[0]

    # Kiểm tra tính toàn vẹn qua CRC16
    data_to_crc = buffer[2 : HEADER_LEN + payload_len]
    actual_crc = crc16_ccitt(data_to_crc)

    consumed = header_idx + total_frame_len
    if actual_crc != expected_crc:
        # Lỗi checksum -> loại bỏ header hỏng để tìm header kế tiếp
        return None, header_idx + 2, f"Lỗi CRC16: nhận {expected_crc:#06x}, tính toán {actual_crc:#06x}"

    now_ms = int(time.time() * 1000)
    frame = FdFrame(
        frame_type=frame_type,
        seq=seq,
        payload=payload,
        crc=actual_crc,
        ts_ms=now_ms,
    )
    return frame, consumed, None
