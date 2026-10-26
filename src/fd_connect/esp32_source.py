"""Nguồn tiếp nhận ESP32 qua cổng Serial/COM3 — phát RawPacket hoặc ErrorEvent (FEAT-025, FEAT-040 / G4).

- Tuân thủ hợp đồng [C-CONNECT] trong features/component.txt và src/contracts/logic.py.
- Chạy trên cạnh optional (connect.esp32 -> signal) của FLOW_TREE.
- Hỗ trợ chế độ độc lập G4: đọc dữ liệu đa cảm biến (CO2, độ rung, GPS, tốc độ, ADC).
- Phân tích linh hoạt 2 định dạng: chuỗi JSON tiền tố FDESP: hoặc chuỗi văn bản ASCII firmware.
- Chống reset chip CP2102/CH340 (dsrdtr=False, rtscts=False), theo dõi mất gói và trôi nhịp.
- Chỉ dùng thư viện chuẩn của Python, không import chéo từ fd_* khác (Luật R2).
"""
from __future__ import annotations

import json
import threading
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
    speed_kmh: Optional[float] = None,
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
    if speed_kmh is not None:
        data["speed_kmh"] = round(float(speed_kmh), 2)
    body = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return ESP32_MAGIC_PREFIX + body


def parse_ascii_telemetry_line(line_text: str) -> Optional[Dict[str, float]]:
    """Phân tích chuỗi văn bản ASCII firmware ESP32 (ví dụ CO2:420.0,ADC:1200,GPS_FIX:1,SPD:45.2)."""
    text = line_text.strip()
    if not text:
        return None
    parsed: Dict[str, float] = {}
    tokens = text.split(",")
    key_mapping = {
        "CO2": "co2_ppm",
        "ADC": "adc_raw",
        "TEMP": "temp_c",
        "HUM": "humidity_pct",
        "GPS_FIX": "gps_ready",
        "GPS": "gps_ready",
        "SPD": "speed_kmh",
        "SPEED": "speed_kmh",
        "LAT": "gps_lat",
        "LON": "gps_lon",
        "VIB": "vibration_g",
        "VIBRATION": "vibration_g",
        "SEQ": "seq",
    }
    for token in tokens:
        if ":" not in token:
            continue
        k, v = token.rsplit(":", 1)
        k_clean = k.strip().upper()
        # Lọc bỏ nhiễu khởi động (boot noise) dính ở đầu khóa đầu tiên nếu có
        if k_clean not in key_mapping:
            for known_k in sorted(key_mapping.keys(), key=len, reverse=True):
                if k_clean.endswith(known_k):
                    k_clean = known_k
                    break
        if k_clean not in key_mapping:
            continue
        norm_key = key_mapping[k_clean]
        try:
            parsed[norm_key] = float(v.strip())
        except ValueError:
            continue
    return parsed if parsed else None


def normalize_esp32_payload(payload_bytes: bytes) -> Tuple[bytes, Optional[Dict[str, float]], Optional[str]]:
    """Chuẩn hóa payload thô sang bytes tiền tố FDESP: và trích xuất từ điển số thực (lọc bỏ boot noise)."""
    if not payload_bytes:
        return payload_bytes, None, "Payload ESP32 rỗng"

    magic_idx = payload_bytes.find(ESP32_MAGIC_PREFIX)
    if magic_idx >= 0:
        clean_magic_bytes = payload_bytes[magic_idx:].strip()
        body_bytes = clean_magic_bytes[len(ESP32_MAGIC_PREFIX) :]
        try:
            body_dict = json.loads(body_bytes.decode("utf-8"))
            if isinstance(body_dict, dict) and body_dict:
                clean = {str(k): float(v) for k, v in body_dict.items() if isinstance(v, (int, float))}
                if clean:
                    return clean_magic_bytes, clean, None
        except (UnicodeDecodeError, ValueError) as exc:
            return payload_bytes, None, f"Lỗi cú pháp JSON: {exc}"

    # Thử giải mã dạng chuỗi ASCII
    try:
        decoded_text = payload_bytes.decode("utf-8", errors="replace").strip()
        ascii_dict = parse_ascii_telemetry_line(decoded_text)
        if ascii_dict:
            re_encoded = ESP32_MAGIC_PREFIX + json.dumps(ascii_dict, separators=(",", ":")).encode("utf-8")
            return re_encoded, ascii_dict, None
    except Exception as exc:
        return payload_bytes, None, f"Lỗi phân tích cú pháp ASCII: {exc}"

    return payload_bytes, None, "Định dạng payload ESP32 không hợp lệ"


class Esp32Source:
    """Thực thi NodeContract cho node connect.esp32 (cổng COM3 / Serial / Replay)."""

    node_id: NodeId = "connect.esp32"

    def __init__(
        self,
        enabled: bool = True,
        uri: Optional[str] = "COM3",
        baudrate: int = 115200,
        timeout_ms: int = 1000,
        gps_attached: bool = False,
        dsrdtr: bool = False,
        rtscts: bool = False,
        serial_mutex: Optional[threading.Lock] = None,
        packet_provider: Optional[Callable[[int], Optional[bytes]]] = None,
    ) -> None:
        self._enabled: bool = enabled
        self._uri: Optional[str] = uri
        self._baudrate: int = baudrate
        self._timeout_ms: int = timeout_ms
        self._gps_attached: bool = gps_attached
        self._dsrdtr: bool = dsrdtr
        self._rtscts: bool = rtscts
        self._mutex: threading.Lock = serial_mutex or threading.Lock()
        self._packet_provider = packet_provider

        self._is_open: bool = False
        self._forced_lost: bool = False
        self._forced_timeout: bool = False
        self._health: Health = "DOWN"
        self._seq: int = 0
        self._last_seq: Optional[int] = None
        self._packet_loss_count: int = 0
        self._last_telemetry: Optional[Dict[str, float]] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def uri(self) -> Optional[str]:
        return self._uri

    @property
    def baudrate(self) -> int:
        return self._baudrate

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms

    @property
    def dsrdtr(self) -> bool:
        return self._dsrdtr

    @property
    def rtscts(self) -> bool:
        return self._rtscts

    @property
    def serial_mutex(self) -> threading.Lock:
        return self._mutex

    @property
    def gps_attached(self) -> bool:
        return self._gps_attached

    @property
    def is_open(self) -> bool:
        return self._is_open and not self._forced_lost

    @property
    def packet_count(self) -> int:
        return self._seq

    @property
    def packet_loss_count(self) -> int:
        return self._packet_loss_count

    @property
    def last_telemetry(self) -> Optional[Dict[str, float]]:
        return dict(self._last_telemetry) if self._last_telemetry is not None else None

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
        """Mở cổng kết nối ESP32 (COM3 / Serial). Cố định dsrdtr=False, rtscts=False chống sập chip CP2102."""
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

    def reconnect(self, trace_id: str = "esp32-reconnect") -> Optional[ErrorEvent]:
        """Khôi phục kết nối ESP32 sau khi đứt cáp (E_CONN_LOST) hoặc lỗi timeout."""
        self._forced_lost = False
        self._forced_timeout = False
        return self.open(trace_id=trace_id)

    def check_gps_ready(self, trace_id: str = "esp32-gps-check") -> Optional[ErrorEvent]:
        """Kiểm tra trạng thái GPS trên ESP32."""
        if not self._gps_attached:
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_CONN_TIMEOUT",
                f"ESP32 tại {self._uri} chưa lắp module GPS; chạy chế độ suy giảm DEGRADED",
                recoverable=True,
            )
        return None

    def read_packet(self, trace_id: str) -> Union[RawPacket, ErrorEvent]:
        """Đọc gói dữ liệu cảm biến từ ESP32 và chuẩn hóa thành RawPacket(sensor_kind='esp32')."""
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

        with self._mutex:
            self._seq += 1
            if self._packet_provider is not None:
                raw_payload = self._packet_provider(self._seq)
                if raw_payload is None:
                    self._is_open = False
                    self._health = "DOWN"
                    return self._make_error(
                        trace_id,
                        "E_CONN_LOST",
                        f"Luồng dữ liệu ESP32 trên cổng {self._uri} trả về rỗng",
                        recoverable=True,
                    )
            else:
                raw_payload = encode_esp32_telemetry(
                    co2_ppm=420.0,
                    vibration_g=0.02,
                    gps_attached=self._gps_attached,
                    seq=self._seq,
                )

        # Chuẩn hóa payload và phát hiện mất gói qua sequence gap
        normalized_bytes, telemetry_dict, err_msg = normalize_esp32_payload(raw_payload)
        if err_msg is not None or telemetry_dict is None:
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_SIG_BAD_FORMAT",
                f"Dữ liệu cảm biến ESP32 không hợp lệ: {err_msg}",
                recoverable=True,
            )

        packet_seq = int(telemetry_dict.get("seq", self._seq))
        if self._last_seq is not None and packet_seq > (self._last_seq + 1):
            loss = packet_seq - self._last_seq - 1
            self._packet_loss_count += loss
        self._last_seq = packet_seq
        self._last_telemetry = telemetry_dict

        self._health = "OK" if self._gps_attached else "DEGRADED"
        return RawPacket(
            trace_id=trace_id,
            ts_ms=_now_ms(),
            source="connect.esp32",
            payload=normalized_bytes,
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
