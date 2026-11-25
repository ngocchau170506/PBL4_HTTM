"""Pure-stdlib WGS-84 Geodetic & Real GNSS Positioning Math Engine (PI_AI).

Shared Single Source of Truth across `pi_edge/` and `server_ai/` (LCK-03 & R7:
pure Python standard library only, zero external dependencies, zero fake/sample coordinates).

Capabilities:
  - Strict WGS-84 coordinate validation (rejects NaN/Inf, out-of-range, and Null Island 0,0)
  - WGS-84 Geodetic (lat, lon, alt_m) <-> ECEF (X, Y, Z meters)
  - Local Tangent Plane ENU (East, North, Up meters) for distortion-free metric maps
  - WGS-84 UTM projection (Zone, MGRS Latitude Band, Easting, Northing)
  - Haversine geodesic distance (m / km), forward azimuth bearing (0..360 deg), Vietnamese compass
  - DMS formatting (Degrees-Minutes-Seconds)
  - Physical horizontal accuracy estimation from HDOP + fix quality + satellite count
  - Real kinematic outlier rejection & stationary multipath smoothing (NO frozen coords <1.8 km/h!)
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

# WGS-84 Reference Ellipsoid Constants
WGS84_A = 6378137.0                      # Semi-major axis (m)
WGS84_F = 1.0 / 298.257223563            # Flattening
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)     # First eccentricity squared
WGS84_EP2 = WGS84_E2 / (1.0 - WGS84_E2)  # Second eccentricity squared
EARTH_MEAN_RADIUS_M = 6371008.8          # IUGG mean Earth radius (m)

# Consumer GNSS User Equivalent Range Error (UERE, 1-sigma meters) by NMEA fix quality
_UERE_BY_FIX_QUALITY = {
    1: 2.5,   # Standard GPS/GNSS SPS
    2: 1.0,   # DGPS / SBAS
    4: 0.05,  # RTK Fixed
    5: 0.35,  # RTK Float
}

_UTM_BANDS = "CDEFGHJKLMNPQRSTUVWX"
_COMPASS_8_VI = ("Bắc", "Đông Bắc", "Đông", "Đông Nam", "Nam", "Tây Nam", "Tây", "Tây Bắc")


def is_valid_wgs84(lat: Any, lon: Any) -> bool:
    """Return True ONLY for finite, in-bounds, non-Null-Island WGS-84 coordinates."""
    if lat is None or lon is None or isinstance(lat, bool) or isinstance(lon, bool):
        return False
    try:
        la = float(lat)
        lo = float(lon)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(la) and math.isfinite(lo)):
        return False
    if abs(la) > 90.0 or abs(lo) > 180.0:
        return False
    # Reject Null Island (0.0, 0.0) emitted by unfixed NMEA receivers
    if abs(la) < 1e-6 and abs(lo) < 1e-6:
        return False
    return True


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS-84 points in meters."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp * 0.5) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl * 0.5) ** 2
    return 2.0 * EARTH_MEAN_RADIUS_M * math.asin(min(1.0, math.sqrt(max(0.0, a))))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Forward azimuth bearing from point 1 to point 2 in degrees [0, 360)."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    if abs(x) < 1e-15 and abs(y) < 1e-15:
        return 0.0
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def compass_label_vi(heading_deg: Optional[float]) -> str:
    """Convert heading degrees [0..360) into 8-point Vietnamese compass direction."""
    if heading_deg is None or not math.isfinite(float(heading_deg)):
        return "-"
    idx = int(((float(heading_deg) % 360.0) + 22.5) // 45.0) % 8
    return _COMPASS_8_VI[idx]


def geodetic_to_ecef(lat: float, lon: float, alt_m: float = 0.0) -> Tuple[float, float, float]:
    """Convert WGS-84 geodetic (deg, deg, m) to Earth-Centered Earth-Fixed (X, Y, Z) meters."""
    phi = math.radians(lat)
    lam = math.radians(lon)
    sin_p = math.sin(phi)
    cos_p = math.cos(phi)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_p * sin_p)
    h = float(alt_m or 0.0)
    x = (n + h) * cos_p * math.cos(lam)
    y = (n + h) * cos_p * math.sin(lam)
    z = (n * (1.0 - WGS84_E2) + h) * sin_p
    return x, y, z


def ecef_to_geodetic(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """Convert ECEF (X, Y, Z meters) back to WGS-84 (lat_deg, lon_deg, alt_m) via iterative Bowring."""
    p = math.hypot(x, y)
    lon = math.atan2(y, x)
    if p < 1e-9:
        lat = math.copysign(math.pi * 0.5, z)
        b = WGS84_A * (1.0 - WGS84_F)
        alt = abs(z) - b
        return math.degrees(lat), math.degrees(lon), alt
    phi = math.atan2(z, p * (1.0 - WGS84_E2))
    n = WGS84_A
    for _ in range(6):
        sin_p = math.sin(phi)
        n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_p * sin_p)
        phi_next = math.atan2(z + WGS84_E2 * n * sin_p, p)
        if abs(phi_next - phi) < 1e-13:
            phi = phi_next
            break
        phi = phi_next
    sin_p = math.sin(phi)
    cos_p = math.cos(phi)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_p * sin_p)
    alt = (p / cos_p - n) if abs(cos_p) > 1e-9 else (z / sin_p - n * (1.0 - WGS84_E2))
    return math.degrees(phi), math.degrees(lon), alt


def geodetic_to_enu(
    lat: float,
    lon: float,
    alt_m: float,
    ref_lat: float,
    ref_lon: float,
    ref_alt_m: float = 0.0,
) -> Tuple[float, float, float]:
    """Convert WGS-84 point to Local Tangent Plane (East, North, Up) meters relative to ref origin."""
    x, y, z = geodetic_to_ecef(lat, lon, alt_m)
    x0, y0, z0 = geodetic_to_ecef(ref_lat, ref_lon, ref_alt_m)
    dx, dy, dz = x - x0, y - y0, z - z0

    phi = math.radians(ref_lat)
    lam = math.radians(ref_lon)
    sin_p, cos_p = math.sin(phi), math.cos(phi)
    sin_l, cos_l = math.sin(lam), math.cos(lam)

    east = -sin_l * dx + cos_l * dy
    north = -sin_p * cos_l * dx - sin_p * sin_l * dy + cos_p * dz
    up = cos_p * cos_l * dx + cos_p * sin_l * dy + sin_p * dz
    return east, north, up


def geodetic_to_utm(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Project WGS-84 (lat, lon) into UTM (Zone, MGRS Band, Easting m, Northing m)."""
    if not is_valid_wgs84(lat, lon) or lat < -80.0 or lat > 84.0:
        return None
    k0 = 0.9996
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    zone = max(1, min(60, zone))
    band_idx = min(len(_UTM_BANDS) - 1, max(0, int(math.floor((lat + 80.0) / 8.0))))
    band = _UTM_BANDS[band_idx]

    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi = math.radians(lat)
    lam = math.radians(lon)

    sin_p = math.sin(phi)
    cos_p = math.cos(phi)
    tan_p = math.tan(phi)

    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_p * sin_p)
    t = tan_p * tan_p
    c = WGS84_EP2 * cos_p * cos_p
    a_coef = cos_p * (lam - lon0)

    e2 = WGS84_E2
    e4 = e2 * e2
    e6 = e4 * e2
    m = WGS84_A * (
        (1.0 - e2 / 4.0 - 3.0 * e4 / 64.0 - 5.0 * e6 / 256.0) * phi
        - (3.0 * e2 / 8.0 + 3.0 * e4 / 32.0 + 45.0 * e6 / 1024.0) * math.sin(2.0 * phi)
        + (15.0 * e4 / 256.0 + 45.0 * e6 / 1024.0) * math.sin(4.0 * phi)
        - (35.0 * e6 / 3072.0) * math.sin(6.0 * phi)
    )

    a2 = a_coef * a_coef
    a3 = a2 * a_coef
    a4 = a2 * a2
    a5 = a4 * a_coef
    a6 = a4 * a2

    easting = 500000.0 + k0 * n * (
        a_coef
        + (1.0 - t + c) * a3 / 6.0
        + (5.0 - 18.0 * t + t * t + 72.0 * c - 58.0 * WGS84_EP2) * a5 / 120.0
    )
    northing = k0 * (
        m
        + n
        * tan_p
        * (
            a2 / 2.0
            + (5.0 - t + 9.0 * c + 4.0 * c * c) * a4 / 24.0
            + (61.0 - 58.0 * t + t * t + 600.0 * c - 330.0 * WGS84_EP2) * a6 / 720.0
        )
    )
    hemi = "N"
    if lat < 0.0:
        northing += 10000000.0
        hemi = "S"

    return {
        "zone": zone,
        "band": band,
        "hemisphere": hemi,
        "zone_label": f"{zone}{band}",
        "easting_m": round(easting, 2),
        "northing_m": round(northing, 2),
    }


def format_dms(lat: float, lon: float) -> str:
    """Format decimal WGS-84 coordinates as human-readable Degrees-Minutes-Seconds."""
    def _one(val: float, pos_h: str, neg_h: str) -> str:
        hemi = pos_h if val >= 0 else neg_h
        av = abs(val)
        deg = int(av)
        rem = (av - deg) * 60.0
        mins = int(rem)
        secs = (rem - mins) * 60.0
        if secs >= 59.95:
            secs = 0.0
            mins += 1
        if mins >= 60:
            mins = 0
            deg += 1
        return f"{deg}°{mins:02d}'{secs:04.1f}\"{hemi}"

    return f"{_one(lat, 'N', 'S')}, {_one(lon, 'E', 'W')}"


def estimate_accuracy_m(
    *,
    reported_accuracy_m: Optional[float] = None,
    hdop: Optional[float] = None,
    fix_quality: Optional[int] = None,
    satellites: Optional[int] = None,
) -> Optional[float]:
    """Compute realistic horizontal positioning accuracy (meters, ~68% CEP) from GNSS telemetry."""
    if reported_accuracy_m is not None:
        try:
            acc = float(reported_accuracy_m)
            if math.isfinite(acc) and acc > 0.0:
                return round(min(5000.0, acc), 1)
        except (TypeError, ValueError):
            pass

    if hdop is not None:
        try:
            h = float(hdop)
            if math.isfinite(h) and 0.3 <= h <= 50.0:
                uere = _UERE_BY_FIX_QUALITY.get(int(fix_quality or 1), 2.5)
                return round(max(0.2, min(500.0, h * uere)), 1)
        except (TypeError, ValueError):
            pass

    if satellites is not None:
        try:
            sats = int(satellites)
            if sats >= 10:
                return 3.5
            if sats >= 7:
                return 5.5
            if sats >= 5:
                return 9.0
            if sats >= 4:
                return 15.0
        except (TypeError, ValueError):
            pass
    return None


def classify_gps_wiring(
    *,
    is_valid: bool,
    satellites: int = 0,
    sats_in_view: Optional[int] = None,
    gps_rx_bytes: Optional[int] = None,
    nmea_ok: Optional[int] = None,
    nmea_bad: Optional[int] = None,
) -> str:
    """Honest diagnostic classification for ESP32 #2 UART2 GPS receiver health."""
    if is_valid:
        return "LOCKED_3D" if satellites >= 4 else "LOCKED_2D"
    if gps_rx_bytes is not None:
        if gps_rx_bytes == 0:
            return "NO_UART2_DATA_CHECK_WIRING"
        if (nmea_ok or 0) == 0 and (nmea_bad or 0) > 0:
            return "BAD_CHECKSUM_CHECK_BAUD_9600"
        if (nmea_ok or 0) == 0:
            return "UART2_BYTES_NO_NMEA_CHECK_BAUD"
        if (sats_in_view or 0) > 0:
            return f"ACQUIRING_FIX_{sats_in_view}_SATS_VISIBLE"
        return "NMEA_OK_WAITING_OPEN_SKY"
    return "WAITING_SATELLITE_LOCK"


def compute_geodetic_summary(
    lat: float,
    lon: float,
    altitude_m: Optional[float] = None,
    heading_deg: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute complete real geodetic representation (WGS-84, DMS, ECEF, UTM, maps links)."""
    alt = float(altitude_m) if (altitude_m is not None and math.isfinite(float(altitude_m))) else 0.0
    x, y, z = geodetic_to_ecef(lat, lon, alt)
    utm = geodetic_to_utm(lat, lon)
    return {
        "lat": round(float(lat), 6),
        "lon": round(float(lon), 6),
        "altitude_m": round(alt, 1) if altitude_m is not None else None,
        "dms": format_dms(lat, lon),
        "compass": compass_label_vi(heading_deg),
        "ecef": {
            "x_m": round(x, 2),
            "y_m": round(y, 2),
            "z_m": round(z, 2),
        },
        "utm": utm,
        "maps_links": {
            "osm_url": f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=17/{lat:.6f}/{lon:.6f}",
            "google_maps_url": f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}",
        },
    }


def filter_kinematic_fix(
    *,
    prev_lat: Optional[float],
    prev_lon: Optional[float],
    prev_ts: Optional[float],
    lat: float,
    lon: float,
    now_ts: float,
    reported_speed_kmh: Optional[float] = None,
    reported_heading_deg: Optional[float] = None,
    accuracy_m: Optional[float] = None,
    max_plausible_kmh: float = 240.0,
) -> Dict[str, Any]:
    """Validate and fuse a real incoming fix against previous fix without ever freezing slow motion.

    Returns:
      {
        "accepted": bool,
        "reason": str,
        "lat": float,
        "lon": float,
        "speed_kmh": float,
        "heading_deg": Optional[float],
        "delta_m": float,
      }
    """
    spd_in = float(reported_speed_kmh) if (reported_speed_kmh is not None and math.isfinite(float(reported_speed_kmh))) else 0.0
    hdg_in = (
        float(reported_heading_deg) % 360.0
        if (reported_heading_deg is not None and math.isfinite(float(reported_heading_deg)))
        else None
    )

    if not is_valid_wgs84(lat, lon):
        return {"accepted": False, "reason": "INVALID_WGS84", "lat": prev_lat, "lon": prev_lon, "speed_kmh": 0.0, "heading_deg": hdg_in, "delta_m": 0.0}

    if not (is_valid_wgs84(prev_lat, prev_lon) and prev_ts is not None):
        return {
            "accepted": True,
            "reason": "FIRST_FIX",
            "lat": float(lat),
            "lon": float(lon),
            "speed_kmh": max(0.0, spd_in),
            "heading_deg": hdg_in,
            "delta_m": 0.0,
        }

    dt = max(0.0, float(now_ts) - float(prev_ts))
    dist_m = haversine_m(float(prev_lat), float(prev_lon), float(lat), float(lon))

    # Reject impossible teleport glitches over real measurement intervals (dt >= 0.5s;
    # avoid rejecting back-to-back queued spool packets arriving milliseconds apart!)
    if 0.5 <= dt <= 30.0:
        implied_kmh = (dist_m / dt) * 3.6
        if implied_kmh > max_plausible_kmh and dist_m > 500.0:
            return {
                "accepted": False,
                "reason": f"OUTLIER_JUMP_{implied_kmh:.0f}KMH",
                "lat": float(prev_lat),
                "lon": float(prev_lon),
                "speed_kmh": 0.0,
                "heading_deg": hdg_in,
                "delta_m": 0.0,
            }

    # Stationary multipath smoothing: if nearly stopped and tiny jitter (< max(3.5m, 0.8*acc)), gently smooth
    # instead of freezing forever! Larger moves (>= threshold) always update immediately even while walking.
    jitter_band_m = max(3.5, 0.8 * float(accuracy_m)) if accuracy_m is not None else 4.0
    if spd_in < 1.5 and dist_m < jitter_band_m and dt <= 15.0:
        out_lat = 0.75 * float(prev_lat) + 0.25 * float(lat)
        out_lon = 0.75 * float(prev_lon) + 0.25 * float(lon)
        return {
            "accepted": True,
            "reason": "STATIONARY_SMOOTHED",
            "lat": out_lat,
            "lon": out_lon,
            "speed_kmh": 0.0,
            "heading_deg": hdg_in,
            "delta_m": 0.0,
        }

    # Compute derived speed / heading if hardware speed or heading is missing while genuinely moving
    derived_spd = ((dist_m / dt) * 3.6) if (dt >= 0.5 and dist_m >= 3.0) else 0.0
    out_spd = spd_in if spd_in >= 1.0 else (derived_spd if derived_spd >= 1.5 else 0.0)
    out_hdg = hdg_in
    if (out_hdg is None or (out_hdg == 0.0 and spd_in < 1.0)) and dist_m >= 4.0:
        out_hdg = initial_bearing_deg(float(prev_lat), float(prev_lon), float(lat), float(lon))

    return {
        "accepted": True,
        "reason": "UPDATED",
        "lat": float(lat),
        "lon": float(lon),
        "speed_kmh": round(max(0.0, out_spd), 1),
        "heading_deg": round(out_hdg, 1) if out_hdg is not None else None,
        "delta_m": round(dist_m, 2),
    }
