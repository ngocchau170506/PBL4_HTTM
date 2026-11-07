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
from fd_signal.mq135_conditioner import MQ135Conditioner, MQ135Reading

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
        mq135_conditioner: Optional[MQ135Conditioner] = None,
    ) -> None:
        self._expected_width = expected_width
        self._expected_height = expected_height
        self._max_drift_ms = max_drift_ms
        self._default_driver_id = default_driver_id
        self._mq135_conditioner = mq135_conditioner if mq135_conditioner is not None else MQ135Conditioner()
        self._frame_id: int = 0
        self._last_ts_ms: Optional[int] = None
        self._latest_sensor: Optional[Dict[str, float]] = None
        self._latest_gps_ts_ms: Optional[int] = None
        self._max_gps_stale_ms: int = 15000
        self._health: Health = "OK"

    @property
    def frame_id(self) -> int:
        return self._frame_id

    @property
    def latest_sensor(self) -> Optional[Dict[str, float]]:
        return dict(self._latest_sensor) if self._latest_sensor is not None else None

    @property
    def latest_gps(self) -> Optional[Dict[str, float]]:
        """Lấy thông tin định vị GPS mới nhất từ luồng cảm biến ESP32."""
        if not self._latest_sensor:
            return None
        gps_keys = {
            "gps_ready", "gps_lat", "gps_lon", "speed_kmh",
            "gps_satellites", "gps_fix", "gps_utc_sec", "gps_stale",
        }
        res = {k: v for k, v in self._latest_sensor.items() if k in gps_keys}
        return res if res else None

    def has_gps_fix(self) -> bool:
        """Kiểm tra hệ thống có tọa độ định vị GPS hợp lệ (fix_quality >= 1)."""
        gps = self.latest_gps
        return bool(gps and gps.get("gps_ready", 0.0) >= 1.0)

    @property
    def latest_air_quality(self) -> Optional[Dict[str, float]]:
        """Lấy thông tin chất lượng không khí MQ135 mới nhất."""
        if not self._latest_sensor:
            return None
        air_keys = {
            "mq135_ready", "mq135_ppm", "mq135_raw",
            "air_quality_level", "air_quality_warn",
        }
        res = {k: v for k, v in self._latest_sensor.items() if k in air_keys}
        return res if res else None

    def is_air_hazardous(self) -> bool:
        """Kiểm tra môi trường không khí khoang lái có ở mức nguy hiểm không."""
        air = self.latest_air_quality
        return bool(air and air.get("air_quality_level", 0.0) >= 2.0)

    def is_mq135_warmed_up(self) -> bool:
        """Kiểm tra cảm biến khí MQ135 đã hoàn tất chu kỳ sấy nóng đầu đo (>20s)."""
        air = self.latest_air_quality
        return bool(air and air.get("mq135_ready", 0.0) >= 1.0)

    def health(self) -> Health:
        return self._health

    def reset_clock(self) -> None:
        self._last_ts_ms = None
        self._health = "OK"
        self._mq135_conditioner.reset()

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

        # Tiền xử lý dữ liệu cảm biến khí MQ135 chống sụt áp và kiểm tra warm-up (FEAT-081)
        has_mq_key = any(k in clean_sensor for k in ("co2_ppm", "adc_raw", "mq135_raw", "mq135_ppm"))
        if has_mq_key:
            clean_sensor = self._mq135_conditioner.condition_sensor_dict(
                clean_sensor, ts_ms=raw_pkt.ts_ms
            )

        # Cập nhật hoặc lưu đệm Last Known Good GPS khi mất sóng vệ tinh tạm thời
        if "gps_ready" in clean_sensor and clean_sensor["gps_ready"] >= 1.0:
            self._latest_gps_ts_ms = _now_ms()
        elif self._latest_sensor and "gps_lat" in self._latest_sensor and self._latest_gps_ts_ms is not None:
            elapsed = _now_ms() - self._latest_gps_ts_ms
            if elapsed <= self._max_gps_stale_ms:
                clean_sensor["gps_ready"] = 1.0
                clean_sensor["gps_lat"] = self._latest_sensor["gps_lat"]
                clean_sensor["gps_lon"] = self._latest_sensor["gps_lon"]
                clean_sensor["gps_satellites"] = self._latest_sensor.get("gps_satellites", 0.0)
                clean_sensor["gps_fix"] = self._latest_sensor.get("gps_fix", 0.0)
                clean_sensor["gps_utc_sec"] = self._latest_sensor.get("gps_utc_sec", float(_now_ms() / 1000.0))
                clean_sensor["gps_stale"] = 1.0

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
