"""Nguồn tiếp nhận ESP32 qua cổng Serial/COM3 — phát RawPacket hoặc ErrorEvent (FEAT-025 / G2.5).

- Tuân thủ hợp đồng [C-CONNECT] trong features/component.txt và src/contracts/logic.py.
- Chạy trên cạnh optional (connect.esp32 -> signal) của FLOW_TREE.
- Hỗ trợ chế độ G2.5: ESP32 đã cắm cổng COM3 nhưng chưa lắp module GPS (gps_attached=False),
  đảm bảo khi thiếu GPS hoặc đứt kết nối ESP32 chỉ chuyển hệ thống sang DEGRADED, không dừng luồng Camera.
"""
from __future__ import annotations

import json
import time
from typing import Callable, Dict, Optional, Tuple, Union

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId, RawPacket

ESP32_MAGIC_PREFIX = b"FDESP:"


def _now_ms() -> int:
    return int(time.time() * 1000)


def encode_esp32_telemetry(
    co2_ppm: float = 420.0,
    vibration_g: float = 0.02,
    gps_attached: bool = False,
    gps_lat: Optional[float] = None,
    gps_lon: Optional[float] = None,
    seq: int = 1,
) -> bytes:
    """Đóng gói bản tin cảm biến ESP32 thành chuỗi byte có tiền tố FDESP: chuẩn hóa."""
    data: Dict[str, float] = {
        "seq": float(max(0, int(seq))),
        "co2_ppm": round(float(co2_ppm), 2),
        "vibration_g": round(float(vibration_g), 4),
        "gps_ready": 1.0 if (gps_attached and gps_lat is not None and gps_lon is not None) else 0.0,
    }
    if gps_attached and gps_lat is not None and gps_lon is not None:
        data["gps_lat"] = round(float(gps_lat), 6)
        data["gps_lon"] = round(float(gps_lon), 6)
    body = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return ESP32_MAGIC_PREFIX + body


class Esp32Source:
    """Thực thi NodeContract cho node connect.esp32 (cổng COM3)."""

    node_id: NodeId = "connect.esp32"

    def __init__(
        self,
        enabled: bool = True,
        uri: Optional[str] = "COM3",
        baudrate: int = 9600,
        timeout_ms: int = 1000,
        gps_attached: bool = False,
        packet_provider: Optional[Callable[[int], Optional[bytes]]] = None,
    ) -> None:
        self._enabled: bool = enabled
        self._uri: Optional[str] = uri
        self._baudrate: int = baudrate
        self._timeout_ms: int = timeout_ms
        self._gps_attached: bool = gps_attached
        self._packet_provider = packet_provider

        self._is_open: bool = False
        self._forced_lost: bool = False
        self._forced_timeout: bool = False
        self._health: Health = "DOWN"
        self._seq: int = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def uri(self) -> Optional[str]:
        return self._uri

    @property
    def gps_attached(self) -> bool:
        return self._gps_attached

    @property
    def is_open(self) -> bool:
        return self._is_open and not self._forced_lost

    @property
    def packet_count(self) -> int:
        return self._seq

    def health(self) -> Health:
        return self._health

    def _make_error(
        self,
        trace_id: str,
        code: ErrorCode,
        message: str,
        recoverable: bool = True,
    ) -> ErrorEvent:
        safe_trace = trace_id if trace_id else "esp32-missing-trace"
        return ErrorEvent(
            trace_id=safe_trace,
            ts_ms=_now_ms(),
            source="connect.esp32",
            code=code,
            node_path="connect.esp32",
            err_class="source",
            message=message,
            recoverable=recoverable,
        )

    def open(self, trace_id: str = "esp32-open") -> Optional[ErrorEvent]:
        """Mở cổng kết nối ESP32 (ví dụ COM3). Nếu chưa gắn GPS thì đặt health='DEGRADED'."""
        if not trace_id or not trace_id.strip():
            self._health = "DOWN"
            return self._make_error("esp32-no-trace", "E_CONN_OPEN", "trace_id không được rỗng", False)

        if not self._enabled:
            self._is_open = False
            self._health = "DOWN"
            return None

        if not isinstance(self._uri, str) or not self._uri.strip():
            self._is_open = False
            self._health = "DOWN"
            return self._make_error(
                trace_id,
                "E_CONN_OPEN",
                "Cổng kết nối esp32.uri (ví dụ COM3) không được rỗng khi enabled=true",
                True,
            )

        if self._baudrate <= 0 or self._timeout_ms <= 0:
            self._is_open = False
            self._health = "DOWN"
            return self._make_error(
                trace_id,
                "E_CONN_OPEN",
                f"Thông số serial không hợp lệ (baudrate={self._baudrate}, timeout={self._timeout_ms}ms)",
                False,
            )

        self._forced_lost = False
        self._forced_timeout = False
        self._is_open = True
        self._health = "OK" if self._gps_attached else "DEGRADED"
        return None

    def close(self) -> None:
        self._is_open = False
        self._health = "DOWN"

    def simulate_disconnect(self) -> None:
        self._forced_lost = True
        self._is_open = False
        self._health = "DOWN"

    def simulate_timeout(self) -> None:
        self._forced_timeout = True
        self._health = "DEGRADED"

    def check_gps_ready(self, trace_id: str = "esp32-gps-check") -> Optional[ErrorEvent]:
        """Kiểm tra trạng thái module GPS trên ESP32.

        Ở giai đoạn G2.5 (chưa lắp GPS), trả về ErrorEvent có thể phục hồi trên node connect.esp32
        để hệ thống ghi nhận trạng thái DEGRADED mà không ảnh hưởng luồng Camera.
        """
        if not self._gps_attached:
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_CONN_TIMEOUT",
                f"ESP32 tại {self._uri} chưa lắp module GPS (chờ G2.5/G4); chạy chế độ suy giảm DEGRADED",
                recoverable=True,
            )
        return None

    def read_packet(self, trace_id: str) -> Union[RawPacket, ErrorEvent]:
        """Đọc gói dữ liệu cảm biến từ ESP32 và đóng gói thành RawPacket(sensor_kind='esp32')."""
        if not trace_id or not trace_id.strip():
            self._health = "DOWN"
            return self._make_error("esp32-no-trace", "E_CONN_OPEN", "trace_id không được rỗng", False)

        if self._forced_lost or not self._is_open:
            self._health = "DOWN"
            return self._make_error(
                trace_id,
                "E_CONN_LOST",
                f"Mất kết nối tới ESP32 qua cổng {self._uri}",
                recoverable=True,
            )

        if self._forced_timeout:
            self._forced_timeout = False
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_CONN_TIMEOUT",
                f"Quá thời gian chờ gói cảm biến ESP32 ({self._timeout_ms}ms) tại {self._uri}",
                recoverable=True,
            )

        self._seq += 1
        if self._packet_provider is not None:
            payload = self._packet_provider(self._seq)
            if payload is None:
                self._is_open = False
                self._health = "DOWN"
                return self._make_error(
                    trace_id,
                    "E_CONN_LOST",
                    f"Luồng dữ liệu ESP32 trên cổng {self._uri} trả về rỗng",
                    recoverable=True,
                )
        else:
            payload = encode_esp32_telemetry(
                co2_ppm=420.0,
                vibration_g=0.02,
                gps_attached=self._gps_attached,
                seq=self._seq,
            )

        self._health = "OK" if self._gps_attached else "DEGRADED"
        return RawPacket(
            trace_id=trace_id,
            ts_ms=_now_ms(),
            source="connect.esp32",
            payload=payload,
            sensor_kind="esp32",
        )

    def self_test(self) -> Tuple[bool, str]:
        probe = Esp32Source(enabled=True, uri="COM3", gps_attached=False)
        err = probe.open(trace_id="esp32-selftest-open")
        if err is not None:
            return False, f"{err.code}: {err.message}"
        pkt = probe.read_packet(trace_id="esp32-selftest-read")
        if isinstance(pkt, ErrorEvent):
            return False, f"{pkt.code}: {pkt.message}"
        return True, f"Esp32Source OK (uri={probe.uri}, health={probe.health()}, gps={probe.gps_attached})"
