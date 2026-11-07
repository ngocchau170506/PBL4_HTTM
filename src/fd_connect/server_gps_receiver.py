"""Bộ tiếp nhận tọa độ XYZ hỗ trợ từ máy chủ (Server-Assisted XYZ Positioning — FEAT-100).

- Giải quyết bài toán xe/Pi hoạt động trong môi trường khuất sóng vệ tinh (hầm, nhà, lab),
  hoặc thiết bị Pi không gắn module GPS phần cứng nhưng vẫn cần dữ liệu định vị 3D (X, Y, Z).
- Tiếp nhận tọa độ định vị mạng (vĩ độ Lat, kinh độ Lon, độ cao Alt, vận tốc speed_kmh).
- Cung cấp cơ chế chuyển mạch hỗn hợp (HybridGpsManager): ưu tiên GPS vệ tinh thật khi có sóng,
  tự động fallback mượt mà sang tọa độ máy chủ khi mất vệ tinh (gps_source="server_xyz").
- Chỉ dùng thư viện chuẩn của Python; không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from fd_connect.gps_parser import GpsNmeaParser, GpsReading


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class ServerGpsCoordinate:
    """Tọa độ không gian 3 chiều và vận tốc từ máy chủ hỗ trợ."""

    lat: float
    lon: float
    alt_m: float = 0.0
    speed_kmh: float = 0.0
    timestamp_ms: int = 0
    accuracy_m: float = 5.0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "alt_m": round(self.alt_m, 2),
            "speed_kmh": round(self.speed_kmh, 2),
            "timestamp_ms": self.timestamp_ms,
            "accuracy_m": round(self.accuracy_m, 2),
            "note": self.note,
        }


class ServerGpsReceiver:
    """Bộ thu và quản lý tọa độ định vị được cung cấp từ máy chủ điều hành."""

    def __init__(self, max_stale_ms: int = 30000) -> None:
        self._max_stale_ms = max(1000, int(max_stale_ms))
        self._last_coordinate: Optional[ServerGpsCoordinate] = None
        self._last_update_ts_ms: Optional[int] = None

    @property
    def last_coordinate(self) -> Optional[ServerGpsCoordinate]:
        return self._last_coordinate

    @property
    def has_valid_position(self) -> bool:
        return self.is_valid()

    def is_valid(self, current_ts_ms: Optional[int] = None) -> bool:
        if self._last_coordinate is None or self._last_update_ts_ms is None:
            return False
        now_ms = current_ts_ms if current_ts_ms is not None else _now_ms()
        return (now_ms - self._last_update_ts_ms) <= self._max_stale_ms

    def inject_xyz(
        self,
        lat: float,
        lon: float,
        alt_m: float = 0.0,
        speed_kmh: float = 0.0,
        accuracy_m: float = 5.0,
        note: str = "server_injected",
        current_ts_ms: Optional[int] = None,
    ) -> GpsReading:
        """Cập nhật trực tiếp tọa độ không gian 3 chiều từ máy chủ vào Pi."""
        now_ms = current_ts_ms if current_ts_ms is not None else _now_ms()
        coord = ServerGpsCoordinate(
            lat=float(lat),
            lon=float(lon),
            alt_m=float(alt_m),
            speed_kmh=float(speed_kmh),
            timestamp_ms=now_ms,
            accuracy_m=float(accuracy_m),
            note=str(note),
        )
        self._last_coordinate = coord
        self._last_update_ts_ms = now_ms
        return self._build_gps_reading(coord, is_stale=False)

    def load_from_dict(
        self,
        data: Dict[str, Any],
        current_ts_ms: Optional[int] = None,
    ) -> Optional[GpsReading]:
        """Nạp tọa độ từ gói dữ liệu JSON do máy chủ phản hồi."""
        try:
            lat = float(data.get("lat", data.get("gps_lat", 0.0)))
            lon = float(data.get("lon", data.get("gps_lon", 0.0)))
            if lat == 0.0 and lon == 0.0:
                return None
            alt_m = float(data.get("alt_m", data.get("gps_alt", data.get("alt", 0.0))))
            speed_kmh = float(data.get("speed_kmh", data.get("speed", 0.0)))
            accuracy_m = float(data.get("accuracy_m", 5.0))
            note = str(data.get("note", "server_dict"))
            return self.inject_xyz(
                lat=lat,
                lon=lon,
                alt_m=alt_m,
                speed_kmh=speed_kmh,
                accuracy_m=accuracy_m,
                note=note,
                current_ts_ms=current_ts_ms,
            )
        except (ValueError, TypeError):
            return None

    def fetch_from_server(
        self,
        server_url: str,
        timeout_sec: float = 1.5,
        current_ts_ms: Optional[int] = None,
    ) -> Optional[GpsReading]:
        """Chủ động thăm dò (poll) tọa độ hiện tại từ endpoint máy chủ."""
        req = urllib.request.Request(
            url=server_url,
            headers={"User-Agent": "Pi-ServerGpsReceiver/1.0", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                if resp.status == 200:
                    payload = json.loads(resp.read().decode("utf-8"))
                    return self.load_from_dict(payload, current_ts_ms=current_ts_ms)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            _ = exc
        return self.get_reading(current_ts_ms=current_ts_ms)

    def get_reading(self, current_ts_ms: Optional[int] = None) -> Optional[GpsReading]:
        """Lấy bản ghi GpsReading chuẩn từ tọa độ máy chủ hiện tại."""
        if self._last_coordinate is None or self._last_update_ts_ms is None:
            return None
        now_ms = current_ts_ms if current_ts_ms is not None else _now_ms()
        elapsed = now_ms - self._last_update_ts_ms
        if elapsed <= self._max_stale_ms:
            return self._build_gps_reading(self._last_coordinate, is_stale=False)
        return self._build_gps_reading(self._last_coordinate, is_stale=True)

    def _build_gps_reading(self, coord: ServerGpsCoordinate, is_stale: bool) -> GpsReading:
        return GpsReading(
            gps_ready=1.0,
            gps_lat=round(coord.lat, 6),
            gps_lon=round(coord.lon, 6),
            speed_kmh=round(coord.speed_kmh, 2),
            gps_satellites=99.0,
            gps_fix=2.0 if not is_stale else 1.0,
            gps_utc_sec=float(coord.timestamp_ms / 1000.0),
            gps_stale=1.0 if is_stale else 0.0,
            gps_alt=round(coord.alt_m, 2),
            gps_source="server_xyz",
        )


class HybridGpsManager:
    """Bộ điều phối định vị đa nguồn: Vệ tinh GNSS thật + Tọa độ XYZ từ máy chủ."""

    def __init__(
        self,
        hardware_parser: Optional[GpsNmeaParser] = None,
        server_receiver: Optional[ServerGpsReceiver] = None,
    ) -> None:
        self._hardware = hardware_parser if hardware_parser is not None else GpsNmeaParser()
        self._server = server_receiver if server_receiver is not None else ServerGpsReceiver()

    @property
    def hardware_parser(self) -> GpsNmeaParser:
        return self._hardware

    @property
    def server_receiver(self) -> ServerGpsReceiver:
        return self._server

    def feed_nmea_sentence(self, sentence: str, current_ts_ms: Optional[int] = None) -> Optional[GpsReading]:
        """Nạp dữ liệu dòng NMEA từ cổng phần cứng Serial."""
        return self._hardware.parse_line(sentence, current_ts_ms=current_ts_ms)

    def feed_server_xyz(
        self,
        lat: float,
        lon: float,
        alt_m: float = 0.0,
        speed_kmh: float = 0.0,
        current_ts_ms: Optional[int] = None,
    ) -> GpsReading:
        """Nạp dữ liệu tọa độ XYZ nhận từ máy chủ."""
        return self._server.inject_xyz(
            lat=lat,
            lon=lon,
            alt_m=alt_m,
            speed_kmh=speed_kmh,
            current_ts_ms=current_ts_ms,
        )

    def get_current_gps(self, current_ts_ms: Optional[int] = None) -> Optional[GpsReading]:
        """Lấy định vị tối ưu theo cơ chế ưu tiên: Vệ tinh GNSS -> Máy chủ XYZ -> Fallback."""
        now_ms = current_ts_ms if current_ts_ms is not None else _now_ms()
        hw_reading = self._hardware.last_reading
        is_hw_fresh = self._hardware.is_fresh(now_ms) if hasattr(self._hardware, "is_fresh") else False

        if is_hw_fresh and hw_reading is not None and hw_reading.gps_fix >= 1.0 and hw_reading.gps_stale == 0.0:
            return hw_reading

        if self._server.is_valid(current_ts_ms=now_ms):
            server_reading = self._server.get_reading(current_ts_ms=now_ms)
            if server_reading is not None:
                return server_reading

        if hw_reading is not None:
            return hw_reading

        return self._server.get_reading(current_ts_ms=now_ms)
