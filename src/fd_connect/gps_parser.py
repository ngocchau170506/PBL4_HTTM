"""Bộ giải mã chuỗi chuẩn NMEA 0183 cho module GPS trên ESP32/UART (FEAT-080).

- Giải mã các câu tin NMEA phổ biến ($GPRMC, $GNRMC, $GPGGA, $GNGGA).
- Tính toán tọa độ thập phân (Decimal Degrees) từ định dạng độ-phút NMEA (ddmm.mmmm).
- Chuyển đổi vận tốc hải lý (knots) sang km/h (1 knot = 1.852 km/h).
- Chuyển đổi dấu thời gian UTC và ngày tháng sang giây Epoch chuẩn.
- Kiểm tra tính toàn vẹn gói tin qua mã kiểm tra NMEA Checksum (XOR).
- Cơ chế lưu đệm tọa độ hợp lệ gần nhất (Last Known Good GPS) khi xe mất sóng.
- Chỉ dùng thư viện chuẩn của Python, không import chéo từ fd_* khác (Luật R2).
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime
import time
from typing import Dict, Optional, Tuple


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class GpsReading:
    """Bản ghi dữ liệu GPS đã chuẩn hóa số thực (phù hợp với Dict[str, float])."""

    gps_ready: float
    gps_lat: float
    gps_lon: float
    speed_kmh: float
    gps_satellites: float
    gps_fix: float
    gps_utc_sec: float
    gps_stale: float = 0.0
    gps_alt: float = 0.0
    gps_source: str = "satellite"

    def to_sensor_dict(self) -> Dict[str, float]:
        """Xuất sang từ điển số thực tuân thủ SignalPacket.sensor."""
        return {
            "gps_ready": float(self.gps_ready),
            "gps_lat": round(float(self.gps_lat), 6),
            "gps_lon": round(float(self.gps_lon), 6),
            "gps_alt": round(float(self.gps_alt), 2),
            "speed_kmh": round(float(self.speed_kmh), 2),
            "gps_satellites": float(self.gps_satellites),
            "gps_fix": float(self.gps_fix),
            "gps_utc_sec": round(float(self.gps_utc_sec), 2),
            "gps_stale": float(self.gps_stale),
            "gps_is_server": 1.0 if self.gps_source == "server_xyz" else 0.0,
        }


def verify_nmea_checksum(sentence: str) -> bool:
    """Kiểm tra mã kiểm tra (checksum) của câu NMEA."""
    clean = sentence.strip()
    if not clean.startswith("$") or "*" not in clean:
        return False
    content, chk_hex = clean[1:].rsplit("*", 1)
    if len(chk_hex) < 2:
        return False
    try:
        expected = int(chk_hex[:2], 16)
    except ValueError:
        return False

    computed = 0
    for char in content:
        computed ^= ord(char)
    return computed == expected


def parse_nmea_coord(raw_val: str, direction: str) -> Optional[float]:
    """Chuyển đổi tọa độ định dạng NMEA (ddmm.mmmm hoặc dddmm.mmmm) sang độ thập phân."""
    val = raw_val.strip()
    direc = direction.strip().upper()
    if not val or direc not in ("N", "S", "E", "W"):
        return None
    try:
        raw_float = float(val)
    except ValueError:
        return None

    # Tách phần độ (degrees) và phần phút (minutes)
    degrees = int(raw_float / 100.0)
    minutes = raw_float - (degrees * 100.0)
    decimal = float(degrees) + (minutes / 60.0)

    if direc in ("S", "W"):
        decimal = -decimal
    return round(decimal, 6)


def parse_nmea_utc_epoch(utc_time_str: str, date_str: Optional[str] = None) -> float:
    """Chuyển đổi giờ UTC (hhmmss.ss) và ngày (ddmmyy) sang giây Unix Epoch."""
    time_part = utc_time_str.strip()
    if len(time_part) < 6:
        return float(time.time())
    try:
        hours = int(time_part[0:2])
        minutes = int(time_part[2:4])
        seconds = int(time_part[4:6])
        microsec = int(float("0." + time_part[7:]) * 1_000_000) if "." in time_part else 0

        year = 2026
        month = 10
        day = 31
        if date_str and len(date_str.strip()) >= 6:
            ds = date_str.strip()
            day = int(ds[0:2])
            month = int(ds[2:4])
            raw_yr = int(ds[4:6])
            year = 2000 + raw_yr if raw_yr < 80 else 1900 + raw_yr

        dt = datetime.datetime(year, month, day, hours, minutes, seconds, microsec, tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except (ValueError, OSError):
        return float(time.time())


class GpsNmeaParser:
    """Bộ giải mã dòng bản tin NMEA cho cổng Serial UART GPS."""

    def __init__(self, max_stale_ms: int = 15000) -> None:
        self._max_stale_ms = max(1000, int(max_stale_ms))
        self._last_good_reading: Optional[GpsReading] = None
        self._last_good_ts_ms: Optional[int] = None

    @property
    def last_reading(self) -> Optional[GpsReading]:
        return self._last_good_reading

    def is_fresh(self, current_ts_ms: Optional[int] = None) -> bool:
        """Kiểm tra bản ghi GPS vệ tinh gần nhất có còn trong ngưỡng thời gian cho phép hay không."""
        if self._last_good_reading is None or self._last_good_ts_ms is None:
            return False
        now_ms = current_ts_ms if current_ts_ms is not None else _now_ms()
        return (now_ms - self._last_good_ts_ms) <= self._max_stale_ms

    def parse_line(self, line: str, current_ts_ms: Optional[int] = None) -> Optional[GpsReading]:
        """Phân tích 1 dòng câu tin NMEA ($GPRMC hoặc $GPGGA) và trả về GpsReading."""
        text = line.strip()
        if not text.startswith("$"):
            return self._apply_stale_fallback(current_ts_ms)

        # Kiểm tra checksum nếu có dấu '*'
        if "*" in text and not verify_nmea_checksum(text):
            return self._apply_stale_fallback(current_ts_ms)

        now_ms = current_ts_ms if current_ts_ms is not None else _now_ms()

        # Loại bỏ tiền tố talker: $GPRMC, $GNRMC, $BDGGA -> lấy mã câu tin RMC / GGA
        body = text[1:].split("*")[0]
        parts = body.split(",")
        if len(parts) < 2:
            return self._apply_stale_fallback(now_ms)

        sentence_type = parts[0].upper()
        if sentence_type.endswith("RMC"):
            return self._parse_rmc(parts, now_ms)
        if sentence_type.endswith("GGA"):
            return self._parse_gga(parts, now_ms)

        return self._apply_stale_fallback(now_ms)

    def _parse_rmc(self, parts: list[str], now_ms: int) -> Optional[GpsReading]:
        """Xử lý câu tin RMC: Recommended Minimum Specific GPS/TRANSIT Data."""
        if len(parts) < 10:
            return self._apply_stale_fallback(now_ms)

        utc_time = parts[1]
        status = parts[2].upper()
        if status != "A":
            return self._apply_stale_fallback(now_ms)

        lat = parse_nmea_coord(parts[3], parts[4])
        lon = parse_nmea_coord(parts[5], parts[6])
        if lat is None or lon is None:
            return self._apply_stale_fallback(now_ms)

        speed_knots = 0.0
        try:
            if parts[7].strip():
                speed_knots = float(parts[7].strip())
        except ValueError:
            speed_knots = 0.0
        speed_kmh = round(speed_knots * 1.852, 2)

        date_str = parts[9] if len(parts) > 9 else None
        utc_epoch = parse_nmea_utc_epoch(utc_time, date_str)

        reading = GpsReading(
            gps_ready=1.0,
            gps_lat=lat,
            gps_lon=lon,
            speed_kmh=speed_kmh,
            gps_satellites=self._last_good_reading.gps_satellites if self._last_good_reading else 8.0,
            gps_fix=1.0,
            gps_utc_sec=utc_epoch,
            gps_stale=0.0,
        )
        self._last_good_reading = reading
        self._last_good_ts_ms = now_ms
        return reading

    def _parse_gga(self, parts: list[str], now_ms: int) -> Optional[GpsReading]:
        """Xử lý câu tin GGA: Global Positioning System Fix Data."""
        if len(parts) < 10:
            return self._apply_stale_fallback(now_ms)

        utc_time = parts[1]
        lat = parse_nmea_coord(parts[2], parts[3])
        lon = parse_nmea_coord(parts[4], parts[5])
        fix_quality = 0.0
        try:
            fix_quality = float(parts[6].strip()) if parts[6].strip() else 0.0
        except ValueError:
            fix_quality = 0.0

        if fix_quality <= 0.0 or lat is None or lon is None:
            return self._apply_stale_fallback(now_ms)

        satellites = 0.0
        try:
            satellites = float(parts[7].strip()) if parts[7].strip() else 0.0
        except ValueError:
            satellites = 0.0

        alt_m = 0.0
        if len(parts) > 9 and parts[9].strip():
            try:
                alt_m = float(parts[9].strip())
            except ValueError:
                alt_m = 0.0

        utc_epoch = parse_nmea_utc_epoch(utc_time, None)
        speed = self._last_good_reading.speed_kmh if self._last_good_reading else 0.0

        reading = GpsReading(
            gps_ready=1.0,
            gps_lat=lat,
            gps_lon=lon,
            speed_kmh=speed,
            gps_satellites=satellites,
            gps_fix=fix_quality,
            gps_utc_sec=utc_epoch,
            gps_stale=0.0,
            gps_alt=alt_m,
            gps_source="satellite",
        )
        self._last_good_reading = reading
        self._last_good_ts_ms = now_ms
        return reading

    def _apply_stale_fallback(self, now_ms: Optional[int]) -> Optional[GpsReading]:
        """Áp dụng cơ chế Last Known Good GPS khi mất vệ tinh trong ngưỡng thời gian cho phép."""
        if self._last_good_reading is None or self._last_good_ts_ms is None:
            return None
        current_time = now_ms if now_ms is not None else _now_ms()
        elapsed = current_time - self._last_good_ts_ms
        if elapsed <= self._max_stale_ms:
            # Còn trong ngưỡng dự phòng: giữ tọa độ cũ, bật cờ gps_stale=1.0
            return GpsReading(
                gps_ready=1.0,
                gps_lat=self._last_good_reading.gps_lat,
                gps_lon=self._last_good_reading.gps_lon,
                speed_kmh=0.0,
                gps_satellites=self._last_good_reading.gps_satellites,
                gps_fix=self._last_good_reading.gps_fix,
                gps_utc_sec=self._last_good_reading.gps_utc_sec,
                gps_stale=1.0,
                gps_alt=self._last_good_reading.gps_alt,
                gps_source=self._last_good_reading.gps_source,
            )
        # Hết hạn lưu đệm (quá max_stale_ms): mất định vị hoàn toàn
        return None
