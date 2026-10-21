"""Bộ chuẩn hóa khung hình thô RawPacket sang SignalPacket & ghép cảm biến ESP32 (FEAT-011, FEAT-025).

- Tuân thủ hợp đồng [C-SIGNAL] trong features/component.txt và src/contracts/logic.py.
- Kiểm tra định dạng header, kích thước, độ sáng (ảnh đen), độ lệch timestamp và gắn frame_id.
- Nhận và giải mã gói RawPacket(sensor_kind='esp32') từ nhánh optional để đính kèm vào SignalPacket.sensor.
- Không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

import json
import struct
import time
from typing import Dict, Optional, Tuple, Union

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId, RawPacket, SignalPacket

EXPECTED_MAGIC = b"FDRW"
ESP32_PREFIX = b"FDESP:"
HEADER_STRUCT = struct.Struct(">4sHHI B")


def _now_ms() -> int:
    return int(time.time() * 1000)


class FrameNormalizer:
    """Thực thi NodeContract cho node signal."""

    node_id: NodeId = "signal"

    def __init__(
        self,
        expected_width: int = 640,
        expected_height: int = 480,
        max_drift_ms: int = 5000,
        default_driver_id: Optional[str] = None,
    ) -> None:
        self._expected_width = expected_width
        self._expected_height = expected_height
        self._max_drift_ms = max_drift_ms
        self._default_driver_id = default_driver_id
        self._frame_id: int = 0
        self._last_ts_ms: Optional[int] = None
        self._latest_sensor: Optional[Dict[str, float]] = None
        self._health: Health = "OK"

    @property
    def frame_id(self) -> int:
        return self._frame_id

    @property
    def latest_sensor(self) -> Optional[Dict[str, float]]:
        return dict(self._latest_sensor) if self._latest_sensor is not None else None

    def health(self) -> Health:
        return self._health

    def reset_clock(self) -> None:
        self._last_ts_ms = None
        self._health = "OK"

    def _make_error(
        self,
        trace_id: str,
        code: ErrorCode,
        message: str,
        recoverable: bool = True,
    ) -> ErrorEvent:
        safe_trace = trace_id if trace_id else "sig-missing-trace"
        return ErrorEvent(
            trace_id=safe_trace,
            ts_ms=_now_ms(),
            source="signal",
            code=code,
            node_path="signal",
            err_class="data",
            message=message,
            recoverable=recoverable,
        )

    def ingest_esp32_packet(
        self, raw_pkt: RawPacket
    ) -> Union[Dict[str, float], ErrorEvent]:
        """Giải mã RawPacket từ connect.esp32 thành từ điển số thực và lưu đệm cho khung hình kế tiếp."""
        if not isinstance(raw_pkt, RawPacket):
            self._health = "DEGRADED"
            return self._make_error(
                "sig-esp-invalid",
                "E_SIG_BAD_FORMAT",
                "Gói cảm biến không phải kiểu RawPacket",
                True,
            )

        if not raw_pkt.trace_id or not raw_pkt.trace_id.strip():
            self._health = "DEGRADED"
            return self._make_error(
                "sig-esp-no-trace",
                "E_SIG_BAD_FORMAT",
                "RawPacket từ ESP32 thiếu trace_id",
                True,
            )

        if raw_pkt.sensor_kind != "esp32":
            self._health = "DEGRADED"
            return self._make_error(
                raw_pkt.trace_id,
                "E_SIG_BAD_FORMAT",
                f"sensor_kind phải là 'esp32', nhận '{raw_pkt.sensor_kind}'",
                True,
            )

        payload = raw_pkt.payload
        if not isinstance(payload, (bytes, bytearray)) or len(payload) == 0:
            self._health = "DEGRADED"
            return self._make_error(
                raw_pkt.trace_id,
                "E_SIG_EMPTY",
                "Payload từ ESP32 rỗng",
                True,
            )

        if not bytes(payload).startswith(ESP32_PREFIX):
            self._health = "DEGRADED"
            return self._make_error(
                raw_pkt.trace_id,
                "E_SIG_BAD_FORMAT",
                "Payload ESP32 thiếu tiền tố FDESP:",
                True,
            )

        body_bytes = bytes(payload)[len(ESP32_PREFIX) :]
        try:
            parsed = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            self._health = "DEGRADED"
            return self._make_error(
                raw_pkt.trace_id,
                "E_SIG_BAD_FORMAT",
                f"Không giải mã được JSON cảm biến ESP32: {exc}",
                True,
            )

        if not isinstance(parsed, dict) or not parsed:
            self._health = "DEGRADED"
            return self._make_error(
                raw_pkt.trace_id,
                "E_SIG_BAD_FORMAT",
                "Dữ liệu JSON từ ESP32 phải là object khác rỗng",
                True,
            )

        clean_sensor: Dict[str, float] = {}
        for k, v in parsed.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                self._health = "DEGRADED"
                return self._make_error(
                    raw_pkt.trace_id,
                    "E_SIG_BAD_FORMAT",
                    f"Trường cảm biến '{k}' phải là số thực, nhận {v!r}",
                    True,
                )
            clean_sensor[str(k)] = float(v)

        self._latest_sensor = clean_sensor
        return dict(clean_sensor)

    def normalize(
        self,
        raw_pkt: RawPacket,
        driver_id: Optional[str] = None,
        sensor_data: Optional[Dict[str, float]] = None,
    ) -> Union[SignalPacket, ErrorEvent]:
        """Chuyển đổi RawPacket thành SignalPacket hoặc phát ErrorEvent đúng mã lỗi."""
        if not isinstance(raw_pkt, RawPacket):
            self._health = "DEGRADED"
            return self._make_error(
                "sig-invalid-pkt",
                "E_SIG_BAD_FORMAT",
                "Đầu vào không phải kiểu RawPacket",
                False,
            )

        if not raw_pkt.trace_id:
            self._health = "DEGRADED"
            return self._make_error(
                "sig-no-trace",
                "E_SIG_BAD_FORMAT",
                "RawPacket thiếu trace_id bắt buộc",
                False,
            )

        payload = raw_pkt.payload
        if not isinstance(payload, (bytes, bytearray)) or len(payload) == 0:
            self._health = "DEGRADED"
            return self._make_error(
                raw_pkt.trace_id,
                "E_SIG_EMPTY",
                "Payload của RawPacket rỗng (0 byte)",
                True,
            )

        if len(payload) < HEADER_STRUCT.size:
            self._health = "DEGRADED"
            return self._make_error(
                raw_pkt.trace_id,
                "E_SIG_BAD_FORMAT",
                f"Payload quá ngắn ({len(payload)}B < {HEADER_STRUCT.size}B header)",
                True,
            )

        magic, width, height, _seq, luma_mean = HEADER_STRUCT.unpack_from(payload, 0)
        if magic != EXPECTED_MAGIC:
            self._health = "DEGRADED"
            return self._make_error(
                raw_pkt.trace_id,
                "E_SIG_BAD_FORMAT",
                "Header khung hình không khớp chữ ký FDRW",
                True,
            )

        if luma_mean == 0:
            self._health = "DEGRADED"
            return self._make_error(
                raw_pkt.trace_id,
                "E_SIG_EMPTY",
                "Khung hình bị đen hoàn toàn (luma_mean=0)",
                True,
            )

        if width != self._expected_width or height != self._expected_height:
            self._health = "DEGRADED"
            return self._make_error(
                raw_pkt.trace_id,
                "E_SIG_BAD_FORMAT",
                f"Kích thước khung {width}x{height} khác cấu hình {self._expected_width}x{self._expected_height}",
                True,
            )

        if self._last_ts_ms is not None:
            delta_ms = raw_pkt.ts_ms - self._last_ts_ms
            if delta_ms < 0 or delta_ms > self._max_drift_ms:
                self._health = "DEGRADED"
                return self._make_error(
                    raw_pkt.trace_id,
                    "E_SIG_TS_DRIFT",
                    f"Timestamp lệch nhịp ({delta_ms}ms so với khung trước, ngưỡng {self._max_drift_ms}ms)",
                    True,
                )

        self._last_ts_ms = raw_pkt.ts_ms
        self._frame_id += 1
        self._health = "OK"

        resolved_driver = driver_id if driver_id is not None else self._default_driver_id
        resolved_sensor = (
            sensor_data
            if sensor_data is not None
            else (dict(self._latest_sensor) if self._latest_sensor is not None else None)
        )
        return SignalPacket(
            trace_id=raw_pkt.trace_id,
            ts_ms=raw_pkt.ts_ms,
            source="signal",
            frame_id=self._frame_id,
            width=width,
            height=height,
            driver_id=resolved_driver,
            sensor=resolved_sensor,
        )

    def self_test(self) -> Tuple[bool, str]:
        sample_payload = HEADER_STRUCT.pack(
            EXPECTED_MAGIC, self._expected_width, self._expected_height, 1, 128
        ) + b"\x80\x80\x80\x80"
        sample_raw = RawPacket(
            trace_id="sig-selftest",
            ts_ms=_now_ms(),
            source="connect.camera",
            payload=sample_payload,
            sensor_kind="camera",
        )
        res = self.normalize(sample_raw)
        if isinstance(res, ErrorEvent):
            return False, f"{res.code}: {res.message}"
        return True, f"FrameNormalizer OK (frame_id={res.frame_id}, {res.width}x{res.height})"
