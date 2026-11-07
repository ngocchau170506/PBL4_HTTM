"""Dịch vụ xử lý dữ liệu định vị GPS (NMEA 0183, ESP32 JSON Telemetry, USB Serial).

Hỗ trợ quy đổi tọa độ NMEA (DDMM.MMMM -> Decimal Degrees), đổi vận tốc Knots -> km/h,
lọc chống trôi khi xe dừng (Stationary Deadband Filter) và tính quãng đường Haversine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Tính khoảng cách thực tế giữa 2 tọa độ địa lý theo đường cong Trái Đất (km)."""
    R = 6371.0  # Bán kính Trái Đất (km)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def parse_nmea_coordinate(raw_val: str, hemisphere: str) -> float:
    """Chuyển đổi chuẩn xác định dạng NMEA (ddmm.mmmm) sang Độ thập phân (Decimal Degrees).
    
    Ví dụ: '1045.7573', 'N' -> 10 + (45.7573 / 60) = 10.762622 độ.
    """
    if not raw_val or len(raw_val) < 4 or "." not in raw_val:
        return 0.0
    try:
        dot_idx = raw_val.index(".")
        if dot_idx < 2:
            return 0.0
        deg_digits = dot_idx - 2
        degrees = float(raw_val[:deg_digits])
        minutes = float(raw_val[deg_digits:])
        decimal = degrees + (minutes / 60.0)
        if hemisphere.upper() in ("S", "W"):
            decimal = -decimal
        return decimal
    except Exception:
        return 0.0


@dataclass
class GpsPoint:
    lat: float = 16.068500
    lon: float = 108.221500
    speed_kmh: float = 0.0
    altitude_m: float = 8.5
    heading_deg: float = 0.0
    satellites: int = 0
    status: str = "NO_FIX"  # NO_FIX, FIX_2D, FIX_3D, SIMULATED, SERVER_XYZ
    source: str = "satellite"  # satellite, server_xyz, simulated
    is_valid: bool = False
    timestamp: float = field(default_factory=time.time)
    trip_distance_km: float = 0.0


def auto_detect_gps_port(exclude_ports: Optional[list] = None) -> Optional[Tuple[str, int]]:
    """Quét cổng nối tiếp cắm GPS phần cứng; bỏ qua cổng ESP32 để tránh kích DTR/RTS gây reset hoặc lệch baudrate."""
    try:
        import serial
        import serial.tools.list_ports
        default_esp = "COM3" if os.name == "nt" else "/dev/ttyUSB0"
        esp_port = os.getenv("ESP_PORT", default_esp).strip()
        excluded = {esp_port.upper(), default_esp.upper()}
        if exclude_ports:
            excluded.update(str(p).strip().upper() for p in exclude_ports if p)
        env_excl = os.getenv("GPS_EXCLUDE_PORTS", "")
        excluded.update(p.strip().upper() for p in env_excl.split(",") if p.strip())

        com_ports = [
            p.device for p in serial.tools.list_ports.comports()
            if "BTHENUM" not in getattr(p, "hwid", "")
            and "Bluetooth" not in getattr(p, "description", "")
            and p.device.upper() not in excluded
        ]
        for rpi_port in ("/dev/ttyUSB1", "/dev/ttyACM0", "/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0"):
            if os.path.exists(rpi_port) and rpi_port not in com_ports and rpi_port.upper() not in excluded:
                com_ports.append(rpi_port)

        for port in com_ports:
            if port.upper() in excluded:
                continue
            for baud in (9600, 115200, 4800, 38400):
                try:
                    with serial.Serial(port, baud, timeout=0.25) as s:
                        data = s.read(256).decode("ascii", errors="replace")
                        if any(tag in data for tag in ("$GP", "$GN", "$BD", "$GL", "$GA")):
                            print(f"[GPS AUTO-DETECT] Tự động phát hiện cảm biến GPS phần cứng tại {port} ({baud} baud)!")
                            return port, baud
                except Exception:
                    pass
    except Exception:
        pass
    return None


class GpsManager:
    """Bộ quản lý và xử lý dữ liệu GPS chuyên dụng."""

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 9600,
        enable_direct_usb: bool = True,
        simulation_mode: bool = False,
        deadband_speed_kmh: float = 1.8,
        exclude_ports: Optional[list] = None,
    ):
        self.port = port or os.getenv("GPS_USB_PORT", "").strip()
        self.baudrate = int(os.getenv("GPS_BAUDRATE", str(baudrate)))
        # GPS_DIRECT_USB=0 tắt hẳn việc quét cổng serial khi GPS đã gắn vào ESP32 (không cần USB GPS riêng)
        self.enable_direct_usb = enable_direct_usb and os.getenv("GPS_DIRECT_USB", "1").strip() != "0"
        self.exclude_ports = list(exclude_ports or [])
        self._last_telemetry_seq: Optional[int] = None
        self.simulation_mode = simulation_mode or (os.getenv("GPS_SIMULATION", "0") == "1")
        self.deadband_speed_kmh = float(os.getenv("GPS_DEADBAND_KMH", str(deadband_speed_kmh)))

        self._lock = threading.RLock()
        self.current = GpsPoint()
        self.last_valid_point: Optional[GpsPoint] = None
        self.total_trip_km: float = 0.0

        self._serial = None
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        if self.simulation_mode:
            self._start_simulation_worker()
        elif self.enable_direct_usb:
            self._start_usb_worker()

    def parse_nmea_sentence(self, sentence: str) -> bool:
        """Phân tích câu NMEA 0183; bỏ qua câu hỏng do nhiễu UART để không ngắt luồng đọc."""
        try:
            return self._parse_nmea_sentence_impl(sentence)
        except (ValueError, IndexError):
            return False

    def _parse_nmea_sentence_impl(self, sentence: str) -> bool:
        clean = sentence.strip()
        if not clean.startswith("$"):
            return False

        body = clean[1:].split("*")[0]
        fields = body.split(",")
        cmd = fields[0].upper()

        updated = False

        if cmd.endswith("RMC") and len(fields) >= 8:
            status = fields[2].upper()
            if status == "A":
                lat = parse_nmea_coordinate(fields[3], fields[4])
                lon = parse_nmea_coordinate(fields[5], fields[6])

                # Tốc độ NMEA tính bằng knots -> đổi sang km/h (1 knot = 1.852 km/h)
                spd_raw = float(fields[7]) if fields[7] else 0.0
                speed_kmh = spd_raw * 1.852
                heading = float(fields[8]) if len(fields) > 8 and fields[8] else self.current.heading_deg

                with self._lock:
                    self._update_position(
                        lat=lat,
                        lon=lon,
                        speed_kmh=speed_kmh,
                        heading_deg=heading,
                        status="FIX_3D" if self.current.satellites >= 4 else "FIX_2D",
                        is_valid=True,
                    )
                updated = True
            else:
                with self._lock:
                    if self.current.status != "SERVER_XYZ" or (time.time() - self.current.timestamp) > 30.0:
                        self.current.is_valid = False
                        self.current.status = "NO_FIX"
                        self.current.speed_kmh = 0.0

        elif cmd.endswith("GGA") and len(fields) >= 10:
            quality = int(fields[6]) if fields[6].isdigit() else 0
            if quality > 0:
                sats = int(fields[7]) if fields[7].isdigit() else self.current.satellites
                alt = float(fields[9]) if fields[9] else self.current.altitude_m

                lat = parse_nmea_coordinate(fields[2], fields[3])
                lon = parse_nmea_coordinate(fields[4], fields[5])

                with self._lock:
                    self.current.satellites = sats
                    self.current.altitude_m = round(alt, 1)
                    if lat != 0.0 and lon != 0.0 and not self.current.is_valid:
                        self._update_position(lat=lat, lon=lon, speed_kmh=self.current.speed_kmh, status="FIX_3D", is_valid=True)
                updated = True

        return updated

    def update_from_telemetry_dict(self, data: Dict[str, Any]):
        """Cập nhật tọa độ khi nhận chuỗi JSON từ ESP32 (FDESP:{\"gps_lat\":...})."""
        # Lọc gói trùng seq để không làm mới timestamp giả khi đọc lại latest_telemetry
        seq = data.get("seq")
        if isinstance(seq, (int, float)) and not isinstance(seq, bool):
            with self._lock:
                if self._last_telemetry_seq is not None and int(seq) == self._last_telemetry_seq:
                    return
                self._last_telemetry_seq = int(seq)
        lat = data.get("gps_lat", data.get("lat"))
        lon = data.get("gps_lon", data.get("lon"))
        spd = data.get("speed_kmh", data.get("spd", 0.0))
        alt = data.get("altitude", data.get("altitude_m", self.current.altitude_m))
        head = data.get("heading", data.get("heading_deg", self.current.heading_deg))
        sats = data.get("sats", data.get("satellites", self.current.satellites))
        ready = data.get("gps_ready", data.get("is_valid", "0.0"))

        is_valid = (ready in (1, 1.0, "1.0", "1", True)) and (lat is not None and float(lat) != 0.0)
        with self._lock:
            if is_valid:
                self._update_position(
                    lat=float(lat),
                    lon=float(lon),
                    speed_kmh=float(spd),
                    altitude_m=float(alt),
                    heading_deg=float(head),
                    satellites=int(sats),
                    status="FIX_3D" if int(sats) >= 4 else "FIX_2D",
                    is_valid=True,
                )
            else:
                if self.current.status != "SERVER_XYZ" or (time.time() - self.current.timestamp) > 30.0:
                    self.current.is_valid = False
                    self.current.status = "NO_FIX"
                    self.current.speed_kmh = 0.0

    def _update_position(
        self,
        lat: float,
        lon: float,
        speed_kmh: float,
        altitude_m: Optional[float] = None,
        heading_deg: Optional[float] = None,
        satellites: Optional[int] = None,
        status: str = "FIX_3D",
        source: str = "satellite",
        is_valid: bool = True,
    ):
        """Cập nhật vị trí có tích hợp bộ lọc chống trôi Deadband & tính Odometer."""
        # Stationary Deadband Filter: ép vận tốc < ngưỡng về 0.0 km/h và giữ tọa độ vệ tinh gần nhất (<= 10s)
        if speed_kmh < self.deadband_speed_kmh:
            speed_kmh = 0.0
            lvp = self.last_valid_point
            if (
                lvp is not None
                and lvp.source == "satellite"
                and (time.time() - lvp.timestamp) <= 10.0
            ):
                lat = lvp.lat
                lon = lvp.lon

        if self.last_valid_point is not None and speed_kmh > 0.0:
            delta_d = haversine_distance_km(self.last_valid_point.lat, self.last_valid_point.lon, lat, lon)
            if 0.002 <= delta_d <= 0.5:  # Giới hạn 2m..500m mỗi nhịp để loại trừ bước nhảy GPS ảo
                self.total_trip_km += delta_d

        self.current.lat = round(lat, 6)
        self.current.lon = round(lon, 6)
        self.current.speed_kmh = round(speed_kmh, 1)
        if altitude_m is not None:
            self.current.altitude_m = round(altitude_m, 1)
        if heading_deg is not None:
            self.current.heading_deg = round(heading_deg, 1)
        if satellites is not None:
            self.current.satellites = satellites
        self.current.status = status
        self.current.source = source
        self.current.is_valid = is_valid
        self.current.timestamp = time.time()
        self.current.trip_distance_km = round(self.total_trip_km, 2)

        self.last_valid_point = GpsPoint(
            lat=self.current.lat,
            lon=self.current.lon,
            speed_kmh=self.current.speed_kmh,
            altitude_m=self.current.altitude_m,
            heading_deg=self.current.heading_deg,
            satellites=self.current.satellites,
            status=self.current.status,
            source=self.current.source,
            is_valid=True,
            timestamp=self.current.timestamp,
            trip_distance_km=self.current.trip_distance_km,
        )

    def inject_server_xyz(
        self,
        lat: float,
        lon: float,
        alt_m: float = 0.0,
        speed_kmh: float = 0.0,
    ) -> bool:
        """Nạp tọa độ định vị XYZ từ máy chủ hỗ trợ khi chưa có sóng vệ tinh."""
        with self._lock:
            # Ưu tiên GPS vệ tinh thật nếu đang bắt được từ 4 vệ tinh trở lên
            if self.current.is_valid and self.current.status in ("FIX_2D", "FIX_3D") and self.current.satellites >= 4:
                return False
            self._update_position(
                lat=float(lat),
                lon=float(lon),
                speed_kmh=float(speed_kmh),
                altitude_m=float(alt_m),
                status="SERVER_XYZ",
                source="server_xyz",
                is_valid=True,
                satellites=99,
            )
            return True

    def update_from_server_xyz(
        self,
        x: float = 108.2215,
        y: float = 16.0685,
        z: float = 8.5,
        spd: float = 0.0,
    ) -> bool:
        """Alias cho inject_server_xyz tiếp nhận x(lon), y(lat), z(alt)."""
        return self.inject_server_xyz(lat=y, lon=x, alt_m=z, speed_kmh=spd)

    def get_snapshot(self) -> Dict[str, Any]:
        """Xuất gói dữ liệu GPS chuẩn hóa."""
        with self._lock:
            p = self.current
            stale_threshold = 30.0 if p.status == "SERVER_XYZ" else 5.0
            if time.time() - p.timestamp > stale_threshold and p.status != "SIMULATED":
                p.is_valid = False
                p.status = "NO_FIX"
                p.speed_kmh = 0.0

            return {
                "lat": p.lat,
                "lon": p.lon,
                "x": p.lon,
                "y": p.lat,
                "z": p.altitude_m,
                "speed_kmh": p.speed_kmh,
                "altitude_m": p.altitude_m,
                "alt_m": p.altitude_m,
                "heading_deg": p.heading_deg,
                "satellites": p.satellites,
                "status": p.status,
                "source": getattr(p, "source", "server_xyz" if p.status == "SERVER_XYZ" else "satellite"),
                "trip_km": p.trip_distance_km,
                "is_valid": p.is_valid,
                "google_maps_url": f"https://www.google.com/maps?q={p.lat},{p.lon}",
            }

    def _start_simulation_worker(self):
        """Chạy lộ trình di chuyển mô phỏng phục vụ kiểm thử."""
        def _sim_loop():
            base_lat = 10.772500
            base_lon = 106.698000
            step = 0
            while not self._stop_event.is_set():
                angle = step * 0.05
                lat = base_lat + 0.008 * math.sin(angle)
                lon = base_lon + 0.012 * math.cos(angle)
                spd = 45.0 + 15.0 * math.sin(angle * 2.0)
                heading = (math.degrees(angle) + 90.0) % 360.0

                with self._lock:
                    self._update_position(
                        lat=lat,
                        lon=lon,
                        speed_kmh=spd,
                        altitude_m=12.0,
                        heading_deg=heading,
                        satellites=9,
                        status="SIMULATED",
                        is_valid=True,
                    )
                step += 1
                self._stop_event.wait(1.0)

        self._worker_thread = threading.Thread(target=_sim_loop, name="gps-simulator", daemon=True)
        self._worker_thread.start()

    def _start_usb_worker(self):
        def _usb_loop():
            try:
                import serial
            except ImportError:
                print("[GPS USB] Chưa cài đặt thư viện pyserial")
                return

            last_scan_time = 0.0
            while not self._stop_event.is_set():
                try:
                    if not self.port:
                        if time.time() - last_scan_time >= 5.0:
                            last_scan_time = time.time()
                            detected = auto_detect_gps_port(exclude_ports=self.exclude_ports)
                            if detected:
                                self.port, self.baudrate = detected
                                print(f"[GPS PHẦN CỨNG] Đã tìm thấy cảm biến tại {self.port} ({self.baudrate} baud)")
                            else:
                                self._stop_event.wait(1.0)
                                continue
                        else:
                            self._stop_event.wait(1.0)
                            continue

                    if self._serial is None and self.port:
                        self._serial = serial.Serial(self.port, self.baudrate, timeout=1.0)
                        print(f"[GPS PHẦN CỨNG] Đã kết nối thành công cổng {self.port} ({self.baudrate} baud)")

                    if self._serial:
                        line = self._serial.readline().decode("ascii", errors="replace")
                        if line:
                            self.parse_nmea_sentence(line)
                except Exception:
                    if self._serial:
                        try:
                            self._serial.close()
                        except Exception:
                            pass
                        self._serial = None
                    self.port = ""
                    self._stop_event.wait(2.0)

        self._worker_thread = threading.Thread(target=_usb_loop, name="gps-usb-reader", daemon=True)
        self._worker_thread.start()

    def close(self):
        self._stop_event.set()
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.5)
