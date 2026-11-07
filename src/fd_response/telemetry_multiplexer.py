"""Bộ thu gom và đóng gói toàn diện dòng tín hiệu cảm biến trên Pi (FEAT-101).

- Đóng gói đồng bộ 5 nhóm tín hiệu:
  1. esp: Vi điều khiển ESP32 (seq, độ rung vibration_g, adc_raw, kết nối).
  2. mq135: Chất lượng không khí (co2_ppm, mq135_ppm, mức cảnh báo khí độc).
  3. gps: Định vị không gian 3 chiều XYZ (lat, lon, alt_m, speed_kmh, nguồn vệ tinh/server).
  4. loa: Trạng thái âm thanh cảnh báo (alert_level, buzzer_active, volume_pct, ack).
  5. pi: Sức khỏe hệ thống Pi (nhiệt độ CPU, RAM, camera FPS, trạng thái AI).
- Xuất bản gói tin MultiplexedTelemetryPayload sẵn sàng truyền tải qua mạng về Server đối tác.
- Chỉ dùng thư viện chuẩn của Python; không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class EspTelemetry:
    seq: int = 0
    vibration_g: float = 0.02
    adc_raw: float = 0.0
    is_connected: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "vibration_g": round(self.vibration_g, 4),
            "adc_raw": round(self.adc_raw, 2),
            "is_connected": self.is_connected,
        }


@dataclass(frozen=True)
class Mq135Telemetry:
    co2_ppm: float = 420.0
    mq135_raw: float = 0.0
    mq135_ppm: float = 420.0
    air_quality_level: str = "GOOD"
    air_quality_warn: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "co2_ppm": round(self.co2_ppm, 2),
            "mq135_raw": round(self.mq135_raw, 2),
            "mq135_ppm": round(self.mq135_ppm, 2),
            "air_quality_level": self.air_quality_level,
            "air_quality_warn": self.air_quality_warn,
        }


@dataclass(frozen=True)
class GpsTelemetry:
    lat: float = 10.7725
    lon: float = 106.7012
    alt_m: float = 10.0
    speed_kmh: float = 0.0
    fix: float = 1.0
    satellites: float = 8.0
    source: str = "server_xyz"
    stale: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "alt_m": round(self.alt_m, 2),
            "speed_kmh": round(self.speed_kmh, 2),
            "fix": self.fix,
            "satellites": self.satellites,
            "source": self.source,
            "stale": self.stale,
        }


@dataclass(frozen=True)
class LoaTelemetry:
    alert_level: str = "NORMAL"
    alert_code: str = "NORMAL"
    buzzer_active: bool = False
    volume_pct: int = 80
    last_ack_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_level": self.alert_level,
            "alert_code": self.alert_code,
            "buzzer_active": self.buzzer_active,
            "volume_pct": self.volume_pct,
            "last_ack_ms": self.last_ack_ms,
        }


@dataclass(frozen=True)
class PiTelemetry:
    cpu_temp_c: float = 42.5
    ram_used_mb: float = 45.0
    cpu_usage_pct: float = 12.0
    camera_fps: float = 30.0
    ai_status: str = "RUNNING"
    uptime_sec: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_temp_c": round(self.cpu_temp_c, 1),
            "ram_used_mb": round(self.ram_used_mb, 2),
            "cpu_usage_pct": round(self.cpu_usage_pct, 1),
            "camera_fps": round(self.camera_fps, 2),
            "ai_status": self.ai_status,
            "uptime_sec": self.uptime_sec,
        }


@dataclass(frozen=True)
class MultiplexedTelemetryPayload:
    """Gói dữ liệu đa tín hiệu chuẩn mực truyền về Server đối tác."""

    device_id: str
    trace_id: str
    timestamp_ms: int
    esp: EspTelemetry
    mq135: Mq135Telemetry
    gps: GpsTelemetry
    loa: LoaTelemetry
    pi: PiTelemetry

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "trace_id": self.trace_id,
            "timestamp_ms": self.timestamp_ms,
            "esp": self.esp.to_dict(),
            "mq135": self.mq135.to_dict(),
            "gps": self.gps.to_dict(),
            "loa": self.loa.to_dict(),
            "pi": self.pi.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class TelemetryMultiplexer:
    """Bộ thu thập và hợp nhất các dòng dữ liệu đa cảm biến trên Pi."""

    def __init__(self, device_id: str = "PI-EDGE-HCM") -> None:
        self._device_id = device_id
        self._boot_ts_sec = int(time.time())
        self._esp = EspTelemetry()
        self._mq135 = Mq135Telemetry()
        self._gps = GpsTelemetry()
        self._loa = LoaTelemetry()
        self._pi = PiTelemetry()

    @property
    def device_id(self) -> str:
        return self._device_id

    def update_esp(
        self,
        seq: int = 0,
        vibration_g: float = 0.02,
        adc_raw: float = 0.0,
        is_connected: bool = True,
    ) -> None:
        self._esp = EspTelemetry(
            seq=seq,
            vibration_g=vibration_g,
            adc_raw=adc_raw,
            is_connected=is_connected,
        )

    def update_mq135(
        self,
        co2_ppm: float = 420.0,
        mq135_raw: float = 0.0,
        mq135_ppm: float = 420.0,
        air_quality_level: str = "GOOD",
        air_quality_warn: bool = False,
    ) -> None:
        self._mq135 = Mq135Telemetry(
            co2_ppm=co2_ppm,
            mq135_raw=mq135_raw,
            mq135_ppm=mq135_ppm,
            air_quality_level=air_quality_level,
            air_quality_warn=air_quality_warn,
        )

    def update_gps(
        self,
        lat: float,
        lon: float,
        alt_m: float = 0.0,
        speed_kmh: float = 0.0,
        fix: float = 1.0,
        satellites: float = 8.0,
        source: str = "satellite",
        stale: float = 0.0,
    ) -> None:
        self._gps = GpsTelemetry(
            lat=lat,
            lon=lon,
            alt_m=alt_m,
            speed_kmh=speed_kmh,
            fix=fix,
            satellites=satellites,
            source=source,
            stale=stale,
        )

    def update_loa(
        self,
        alert_level: str = "NORMAL",
        alert_code: str = "NORMAL",
        buzzer_active: bool = False,
        volume_pct: int = 80,
        last_ack_ms: int = 0,
    ) -> None:
        self._loa = LoaTelemetry(
            alert_level=alert_level,
            alert_code=alert_code,
            buzzer_active=buzzer_active,
            volume_pct=volume_pct,
            last_ack_ms=last_ack_ms,
        )

    def update_pi(
        self,
        cpu_temp_c: Optional[float] = None,
        ram_used_mb: Optional[float] = None,
        cpu_usage_pct: Optional[float] = None,
        camera_fps: Optional[float] = None,
        ai_status: Optional[str] = None,
    ) -> None:
        uptime = int(time.time()) - self._boot_ts_sec
        # Đọc nhiệt độ CPU thực tế trên Raspberry Pi nếu có
        measured_temp = cpu_temp_c
        if measured_temp is None:
            thermal_path = "/sys/class/thermal/thermal_zone0/temp"
            if os.path.exists(thermal_path):
                try:
                    with open(thermal_path, "r", encoding="utf-8") as f:
                        measured_temp = float(f.read().strip()) / 1000.0
                except (OSError, ValueError):
                    measured_temp = 42.5
            else:
                measured_temp = 42.5

        self._pi = PiTelemetry(
            cpu_temp_c=measured_temp,
            ram_used_mb=ram_used_mb if ram_used_mb is not None else self._pi.ram_used_mb,
            cpu_usage_pct=cpu_usage_pct if cpu_usage_pct is not None else self._pi.cpu_usage_pct,
            camera_fps=camera_fps if camera_fps is not None else self._pi.camera_fps,
            ai_status=ai_status if ai_status is not None else self._pi.ai_status,
            uptime_sec=uptime,
        )

    def ingest_sensor_dict(self, sensor_dict: Dict[str, Any]) -> None:
        """Nạp nhanh từ điển cảm biến tổng hợp từ SignalPacket.sensor."""
        if not sensor_dict:
            return

        # Nhóm ESP
        seq = int(sensor_dict.get("seq", self._esp.seq))
        vib = float(sensor_dict.get("vibration_g", self._esp.vibration_g))
        adc = float(sensor_dict.get("adc_raw", self._esp.adc_raw))
        self.update_esp(seq=seq, vibration_g=vib, adc_raw=adc, is_connected=True)

        # Nhóm MQ-135
        co2 = float(sensor_dict.get("co2_ppm", self._mq135.co2_ppm))
        mq_raw = float(sensor_dict.get("mq135_raw", self._mq135.mq135_raw))
        mq_ppm = float(sensor_dict.get("mq135_ppm", self._mq135.mq135_ppm))
        warn = bool(sensor_dict.get("air_quality_warn", self._mq135.air_quality_warn))
        lvl = str(sensor_dict.get("air_quality_level", self._mq135.air_quality_level))
        self.update_mq135(co2_ppm=co2, mq135_raw=mq_raw, mq135_ppm=mq_ppm, air_quality_level=lvl, air_quality_warn=warn)

        # Nhóm GPS
        lat = float(sensor_dict.get("gps_lat", self._gps.lat))
        lon = float(sensor_dict.get("gps_lon", self._gps.lon))
        alt = float(sensor_dict.get("gps_alt", self._gps.alt_m))
        spd = float(sensor_dict.get("speed_kmh", self._gps.speed_kmh))
        fix = float(sensor_dict.get("gps_fix", self._gps.fix))
        sats = float(sensor_dict.get("gps_satellites", self._gps.satellites))
        stale = float(sensor_dict.get("gps_stale", self._gps.stale))
        is_server = float(sensor_dict.get("gps_is_server", 0.0)) > 0.0
        src = "server_xyz" if is_server else "satellite"
        self.update_gps(lat=lat, lon=lon, alt_m=alt, speed_kmh=spd, fix=fix, satellites=sats, source=src, stale=stale)

        # Nhóm Loa / Cảnh báo
        buzzer = bool(sensor_dict.get("buzzer_active", self._loa.buzzer_active))
        alert_lvl = str(sensor_dict.get("alert_level", self._loa.alert_level))
        alert_cd = str(sensor_dict.get("alert_code", self._loa.alert_code))
        vol = int(sensor_dict.get("volume_pct", self._loa.volume_pct))
        ack_ms = int(sensor_dict.get("last_ack_ms", self._loa.last_ack_ms))
        self.update_loa(alert_level=alert_lvl, alert_code=alert_cd, buzzer_active=buzzer, volume_pct=vol, last_ack_ms=ack_ms)

    def pack(self, trace_id: Optional[str] = None, current_ts_ms: Optional[int] = None) -> MultiplexedTelemetryPayload:
        """Đóng gói toàn bộ trạng thái cảm biến hiện thời thành gói tin chuẩn."""
        now_ms = current_ts_ms if current_ts_ms is not None else _now_ms()
        tid = trace_id if trace_id else f"tr-stream-{now_ms}"
        return MultiplexedTelemetryPayload(
            device_id=self._device_id,
            trace_id=tid,
            timestamp_ms=now_ms,
            esp=self._esp,
            mq135=self._mq135,
            gps=self._gps,
            loa=self._loa,
            pi=self._pi,
        )
