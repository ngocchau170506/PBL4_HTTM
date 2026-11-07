"""Bộ phát lại dữ liệu cảm biến ESP32 phục vụ kiểm thử và mô phỏng (FEAT-042 / G4).

- Đọc bản tin từ file fixture JSONL hoặc tạo chuỗi cảm biến tổng hợp.
- Cung cấp payload dạng bytes có tiền tố FDESP: hoặc chuỗi văn bản ASCII tương thích firmware.
- Hỗ trợ tiêm lỗi mô phỏng: mất gói (drop), gói sai định dạng (corrupt), và kiểm soát nhịp phát.
- Chỉ dùng thư viện chuẩn của Python, không import chéo từ fd_* khác (Luật R2).
"""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Dict, Iterator, List, Optional, Union

ESP32_MAGIC_PREFIX = b"FDESP:"


def format_telemetry_bytes(
    seq: int,
    co2_ppm: float = 420.0,
    vibration_g: float = 0.02,
    gps_ready: float = 1.0,
    gps_lat: Optional[float] = None,
    gps_lon: Optional[float] = None,
    speed_kmh: Optional[float] = None,
) -> bytes:
    """Đóng gói từ điển cảm biến thành chuỗi bytes FDESP:{json} chuẩn hợp đồng."""
    payload_dict: Dict[str, float] = {
        "seq": float(max(0, int(seq))),
        "co2_ppm": round(float(co2_ppm), 2),
        "vibration_g": round(float(vibration_g), 4),
        "gps_ready": 1.0 if float(gps_ready) >= 1.0 else 0.0,
    }
    if gps_lat is not None and gps_lon is not None:
        payload_dict["gps_lat"] = round(float(gps_lat), 6)
        payload_dict["gps_lon"] = round(float(gps_lon), 6)
    if speed_kmh is not None:
        payload_dict["speed_kmh"] = round(float(speed_kmh), 2)
    body = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    return ESP32_MAGIC_PREFIX + body


def format_ascii_telemetry_bytes(
    seq: int,
    co2_ppm: float = 420.0,
    vibration_g: float = 0.02,
    gps_ready: float = 1.0,
    gps_lat: Optional[float] = None,
    gps_lon: Optional[float] = None,
    speed_kmh: Optional[float] = None,
) -> bytes:
    """Đóng gói bản tin cảm biến sang chuỗi ASCII tương thích firmware ESP32."""
    parts = [
        f"CO2:{float(co2_ppm):.1f}",
        "ADC:1200",
        f"VIB:{float(vibration_g):.4f}",
        f"GPS_FIX:{1 if float(gps_ready) >= 1.0 else 0}",
    ]
    if speed_kmh is not None:
        parts.append(f"SPD:{float(speed_kmh):.1f}")
    if gps_lat is not None and gps_lon is not None:
        parts.append(f"LAT:{float(gps_lat):.6f}")
        parts.append(f"LON:{float(gps_lon):.6f}")
    parts.append(f"SEQ:{max(0, int(seq))}")
    return (",".join(parts) + "\n").encode("utf-8")


class SensorReplay:
    """Nguồn phát lại dữ liệu cảm biến từ fixture file hoặc bộ nhớ đệm."""

    def __init__(
        self,
        fixture_path: Optional[Union[str, Path]] = None,
        loop: bool = True,
        speed_multiplier: float = 0.0,
        drop_every_n: int = 0,
        corrupt_every_n: int = 0,
        output_ascii: bool = False,
    ) -> None:
        self._fixture_path = Path(fixture_path) if fixture_path else None
        self._loop = bool(loop)
        self._speed_multiplier = max(0.0, float(speed_multiplier))
        self._drop_every_n = max(0, int(drop_every_n))
        self._corrupt_every_n = max(0, int(corrupt_every_n))
        self._output_ascii = bool(output_ascii)

        self._records: List[Dict[str, float]] = []
        self._index: int = 0
        self._emitted_count: int = 0
        self._dropped_count: int = 0
        self._corrupted_count: int = 0

        if self._fixture_path is not None and self._fixture_path.is_file():
            self._load_fixture()

    @property
    def total_records(self) -> int:
        return len(self._records)

    @property
    def emitted_count(self) -> int:
        return self._emitted_count

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def corrupted_count(self) -> int:
        return self._corrupted_count

    def iter_streaming_records(self) -> Iterator[Dict[str, float]]:
        """Đọc streaming từng dòng từ file fixture (generator) không nạp toàn bộ vào RAM."""
        if not self._fixture_path or not self._fixture_path.is_file():
            return
        with open(self._fixture_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                    if isinstance(obj, dict):
                        float_record = {
                            str(k): float(v) for k, v in obj.items() if isinstance(v, (int, float))
                        }
                        if float_record:
                            yield float_record
                except (ValueError, TypeError):
                    continue

    def _load_fixture(self) -> None:
        """Đọc và kiểm tra từng dòng file JSONL qua bộ đọc streaming."""
        self._records = list(self.iter_streaming_records())

    def add_record(self, record: Dict[str, float]) -> None:
        """Thêm một bản ghi số liệu cảm biến vào bộ đệm."""
        float_record = {str(k): float(v) for k, v in record.items() if isinstance(v, (int, float))}
        if float_record:
            self._records.append(float_record)

    def reset(self) -> None:
        """Đặt lại vị trí con trỏ phát lại."""
        self._index = 0
        self._emitted_count = 0
        self._dropped_count = 0
        self._corrupted_count = 0

    def next_packet_payload(self, seq: int) -> Optional[bytes]:
        """Sinh ra chuỗi bytes cho `Esp32Source` với các chính sách tiêm lỗi."""
        # 1. Kiểm tra chính sách tiêm lỗi mất gói (drop)
        if self._drop_every_n > 0 and (seq % self._drop_every_n == 0):
            self._dropped_count += 1
            return None

        # 2. Kiểm tra chính sách tiêm lỗi gói hỏng (corrupt)
        if self._corrupt_every_n > 0 and (seq % self._corrupt_every_n == 0):
            self._corrupted_count += 1
            return b"FDESP:{corrupted_json_not_valid"

        # 3. Lấy dữ liệu từ fixture hoặc sinh mặc định
        formatter = format_ascii_telemetry_bytes if self._output_ascii else format_telemetry_bytes
        if self._records:
            if self._index >= len(self._records):
                if self._loop:
                    self._index = 0
                else:
                    return None
            rec = self._records[self._index]
            self._index += 1
            payload = formatter(
                seq=seq,
                co2_ppm=rec.get("co2_ppm", 420.0),
                vibration_g=rec.get("vibration_g", 0.02),
                gps_ready=rec.get("gps_ready", 1.0),
                gps_lat=rec.get("gps_lat"),
                gps_lon=rec.get("gps_lon"),
                speed_kmh=rec.get("speed_kmh"),
            )
        else:
            payload = formatter(seq=seq, co2_ppm=420.0, vibration_g=0.02, gps_ready=1.0)

        # 4. Giữ nhịp độ trễ nếu có speed_multiplier > 0
        if self._speed_multiplier > 0.0:
            time.sleep(0.01 / self._speed_multiplier)

        self._emitted_count += 1
        return payload
