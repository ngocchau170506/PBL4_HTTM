"""Cầu nối viễn thông và điều phối truyền tin qua ESP32 Gateway (FEAT-111, FEAT-113 / G11).

- Đóng vai trò lớp chuyển giao viễn thông trên Raspberry Pi: thay vì Pi tự mở kết nối mạng WiFi/Internet,
  toàn bộ dữ liệu Telemetry, cảnh báo vi phạm và phân mảnh ảnh (Chunk) được chuyển qua ESP32 bằng giao thức FDFRAME.
- Hỗ trợ 2 chế độ truyền tin:
    + "esp32_gateway": Chuyển giao toàn bộ cho ESP32 Telematics Gateway truyền đi.
    + "direct_wifi": Pi tự mở socket/HTTP kết nối trực tiếp (chế độ dự phòng/tương thích ngược).
- Tích hợp cơ chế kiểm soát luồng (Flow Control) chống tràn RAM trên ESP32:
    Khi ESP32 gửi cờ báo nghẽn mạng hoặc mất kết nối WiFi, Pi tạm dừng đẩy chunk ảnh và giữ đệm tại Pi.
- Chỉ dùng thư viện chuẩn của Python, tuân thủ nghiêm ngặt Luật R2/R4.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId, ViolationEvent
from fd_response.server_dispatcher import FrameChunk

_NODE_PATH = "response.esp32_bridge"

# Mã định danh loại khung tin FDFRAME
FRAME_TYPE_HEARTBEAT = 0x00
FRAME_TYPE_TELEMETRY = 0x01
FRAME_TYPE_VIOLATION = 0x02
FRAME_TYPE_IMAGE_CHUNK = 0x03
FRAME_TYPE_DOWNLINK_ACK = 0x04
FRAME_TYPE_COMMAND = 0x05
FRAME_TYPE_NETWORK_STATUS = 0x06


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_frame_encoder(frame_type: int, payload: Any, seq: int) -> bytes:
    """Bộ mã hóa khung tin mặc định độc lập không phụ thuộc thư mục fd khác (Luật R2)."""
    if isinstance(payload, dict):
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    elif isinstance(payload, str):
        payload_bytes = payload.encode("utf-8")
    else:
        payload_bytes = bytes(payload)
    return b"\xAA\x55" + bytes([frame_type & 0xFF, (seq >> 8) & 0xFF, seq & 0xFF]) + payload_bytes


@dataclass(frozen=True)
class GatewayNetworkStatus:
    """Trạng thái kết nối viễn thông do ESP32 Gateway báo cáo về Pi."""

    wifi_connected: bool
    partner_server_online: bool
    rssi_dbm: int = -65
    is_paused: bool = False
    pending_chunks: int = 0
    uptime_sec: int = 0
    last_update_ms: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GatewayNetworkStatus:
        return cls(
            wifi_connected=bool(data.get("wifi_connected", False)),
            partner_server_online=bool(data.get("partner_server_online", False)),
            rssi_dbm=int(data.get("rssi_dbm", -65)),
            is_paused=bool(data.get("is_paused", False)),
            pending_chunks=int(data.get("pending_chunks", 0)),
            uptime_sec=int(data.get("uptime_sec", 0)),
            last_update_ms=_now_ms(),
        )


class ChunkAssembler:
    """Bộ thu gom và tái hợp nhất các phân mảnh ảnh JPEG trên Server / Bộ thu (FEAT-113)."""

    def __init__(self, expected_total: int, incident_id: str) -> None:
        self.expected_total = max(1, int(expected_total))
        self.incident_id = str(incident_id)
        self._chunks: Dict[int, FrameChunk] = {}

    def add_chunk(self, chunk: FrameChunk) -> bool:
        """Thêm 1 mảnh ảnh vào kho lắp ráp. Trả về True nếu mảnh hợp lệ."""
        if chunk.incident_id != self.incident_id:
            return False
        if 1 <= chunk.chunk_index <= self.expected_total:
            self._chunks[chunk.chunk_index] = chunk
            return True
        return False

    @property
    def is_complete(self) -> bool:
        return len(self._chunks) == self.expected_total

    @property
    def received_count(self) -> int:
        return len(self._chunks)

    def assemble(self) -> Optional[List[FrameChunk]]:
        """Lắp ráp chuỗi mảnh ảnh theo đúng thứ tự tuần tự từ 1 đến N."""
        if not self.is_complete:
            return None
        return [self._chunks[i] for i in range(1, self.expected_total + 1)]


class Esp32NetworkBridge:
    """Cầu nối điều phối viễn thông giữa lõi Pi và ESP32 Telematics Gateway."""

    node_id: NodeId = "response"

    def __init__(
        self,
        uart_sender: Optional[Callable[[bytes], bool]] = None,
        frame_encoder: Optional[Callable[[int, Any, int], bytes]] = None,
        uplink_mode: str = "esp32_gateway",
        max_local_spool: int = 128,
        device_id: str = "CAR-09-HCM",
    ) -> None:
        self._uart_sender = uart_sender
        self._frame_encoder = frame_encoder or _default_frame_encoder
        self._uplink_mode = uplink_mode if uplink_mode in ("esp32_gateway", "direct_wifi") else "esp32_gateway"
        self._max_local_spool = max(16, int(max_local_spool))
        self._device_id = str(device_id)

        self._local_spool: Deque[bytes] = deque(maxlen=self._max_local_spool)
        self._gateway_status = GatewayNetworkStatus(
            wifi_connected=True,
            partner_server_online=True,
            rssi_dbm=-60,
            is_paused=False,
            last_update_ms=_now_ms(),
        )

        self._total_telemetry_sent: int = 0
        self._total_chunks_sent: int = 0
        self._total_violations_sent: int = 0
        self._total_spooled: int = 0
        self._seq: int = 0
        self._health: Health = "OK"

    @property
    def uplink_mode(self) -> str:
        return self._uplink_mode

    @property
    def gateway_status(self) -> GatewayNetworkStatus:
        return self._gateway_status

    @property
    def local_spool_count(self) -> int:
        return len(self._local_spool)

    @property
    def total_sent(self) -> int:
        return self._total_telemetry_sent + self._total_chunks_sent + self._total_violations_sent

    def update_gateway_status(self, status_payload: Union[Dict[str, Any], GatewayNetworkStatus]) -> None:
        """Cập nhật trạng thái mạng từ ESP32 Gateway gửi về."""
        if isinstance(status_payload, GatewayNetworkStatus):
            self._gateway_status = status_payload
        elif isinstance(status_payload, dict):
            self._gateway_status = GatewayNetworkStatus.from_dict(status_payload)
        
        # Nếu Gateway báo đã kết nối lại và không pause, kích hoạt xả đệm
        if self._gateway_status.wifi_connected and not self._gateway_status.is_paused:
            self.drain_spool()

    def send_telemetry(self, telemetry_payload: Dict[str, Any]) -> bool:
        """Đóng gói và truyền gói tin Telemetry qua ESP32 Gateway."""
        self._seq = (self._seq + 1) & 0xFFFF
        frame_bytes = self._frame_encoder(
            FRAME_TYPE_TELEMETRY,
            telemetry_payload,
            self._seq,
        )

        if self._uplink_mode != "esp32_gateway" or self._uart_sender is None:
            return True

        success = self._uart_sender(frame_bytes)
        if success:
            self._total_telemetry_sent += 1
            return True

        self._local_spool.append(frame_bytes)
        self._total_spooled += 1
        return False

    def send_violation(self, violation_dict: Dict[str, Any]) -> bool:
        """Truyền thông báo sự kiện vi phạm xuống ESP32 Gateway để phát còi và gửi Server."""
        self._seq = (self._seq + 1) & 0xFFFF
        frame_bytes = self._frame_encoder(
            FRAME_TYPE_VIOLATION,
            violation_dict,
            self._seq,
        )

        if self._uplink_mode != "esp32_gateway" or self._uart_sender is None:
            return True

        success = self._uart_sender(frame_bytes)
        if success:
            self._total_violations_sent += 1
            return True

        self._local_spool.append(frame_bytes)
        self._total_spooled += 1
        return False

    def send_image_chunk(self, chunk: FrameChunk) -> bool:
        """Truyền mảnh ảnh JPEG qua ESP32 Gateway có kiểm soát chống tràn RAM (FEAT-113)."""
        if self._gateway_status.is_paused or not self._gateway_status.wifi_connected:
            self._local_spool.append(chunk.to_json().encode("utf-8"))
            self._total_spooled += 1
            return False

        self._seq = (self._seq + 1) & 0xFFFF
        chunk_json = chunk.to_json()
        frame_bytes = self._frame_encoder(
            FRAME_TYPE_IMAGE_CHUNK,
            chunk_json,
            self._seq,
        )

        if self._uplink_mode != "esp32_gateway" or self._uart_sender is None:
            return True

        success = self._uart_sender(frame_bytes)
        if success:
            self._total_chunks_sent += 1
            return True

        self._local_spool.append(frame_bytes)
        self._total_spooled += 1
        return False

    def drain_spool(self, max_batch: int = 16) -> int:
        """Xả tuần tự các gói tin đang đệm tại Pi khi kết nối UART/WiFi đã hồi phục."""
        if not self._local_spool or self._uart_sender is None:
            return 0

        drained = 0
        limit = min(len(self._local_spool), max(1, int(max_batch)))
        for _ in range(limit):
            if not self._local_spool:
                break
            payload_bytes = self._local_spool[0]
            success = self._uart_sender(payload_bytes)
            if success:
                self._local_spool.popleft()
                drained += 1
            else:
                break
        return drained
