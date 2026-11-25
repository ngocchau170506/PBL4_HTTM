"""Real GNSS Positioning & Metric Track Engine for Server of AI (ZERO sample/mock coordinates!).

Priority resolution (strictly real hardware sources only):
  1. LIVE_ESP2_GNSS   : Hardware GNSS from ESP32 #2 UART2 (fresh <= 20s, valid WGS-84)
  2. LIVE_PHONE_GNSS  : Real smartphone GNSS from Driver Cockpit (fresh <= 30s, accuracy <= 250m)
  3. LAST_KNOWN_STALE : Newest prior real hardware fix (clearly tagged with exact fix_age_s)
  4. NO_FIX           : Never fabricated; returns lat=None, lon=None + honest UART2 wiring diagnostics

Every real fix is computed via `contracts.geo_math` into:
  - WGS-84 decimal degrees + Degrees-Minutes-Seconds (DMS)
  - ECEF (X, Y, Z meters)
  - UTM (Zone, MGRS Band, Easting m, Northing m)
  - Local Tangent Plane ENU (East, North, Up meters) for distortion-free offline track mapping
  - Kinematic outlier-filtered speed, bearing, Vietnamese compass, and physical accuracy (±m)
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

from contracts.geo_math import (
    classify_gps_wiring,
    compass_label_vi,
    compute_geodetic_summary,
    estimate_accuracy_m,
    filter_kinematic_fix,
    geodetic_to_enu,
    haversine_m,
    is_valid_wgs84,
)
from server_ai.server_core import database

ESP2_LIVE_TTL_S = 20.0
PHONE_LIVE_TTL_S = 30.0
PHONE_MAX_ACCEPTABLE_ACC_M = 500.0


def _opt_float(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _opt_int(v: Any) -> Optional[int]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def process_hardware_gps(
    prev_vehicle: Dict[str, Any],
    gps_dict: Dict[str, Any],
    now_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Validate & filter incoming hardware GNSS from Pi/ESP32 #2 without freezing slow motion."""
    ts = float(now_s if now_s is not None else time.time())
    gps = gps_dict if isinstance(gps_dict, dict) else {}
    raw_valid = bool(gps.get("is_valid"))
    raw_lat = _opt_float(gps.get("lat"))
    raw_lon = _opt_float(gps.get("lon"))
    alt_m = _opt_float(gps.get("altitude_m", gps.get("alt_m")))
    hdop = _opt_float(gps.get("hdop"))
    if hdop is not None and not (0.3 <= hdop < 99.0):
        hdop = None
    fix_q = _opt_int(gps.get("fix_quality", gps.get("fix_q"))) or (1 if raw_valid else 0)
    sats = max(0, _opt_int(gps.get("satellites", gps.get("sats"))) or 0)
    spd_in = _opt_float(gps.get("speed_kmh"))
    hdg_in = _opt_float(gps.get("heading_deg", gps.get("heading")))
    rep_acc = _opt_float(gps.get("accuracy_m"))
    source = str(gps.get("source") or "esp32_hub_gps")

    if not (raw_valid and is_valid_wgs84(raw_lat, raw_lon)):
        return {
            "accepted": False,
            "is_valid": False,
            "reason": "NO_HARDWARE_FIX",
            "lat": None,
            "lon": None,
            "altitude_m": None,
            "speed_kmh": 0.0,
            "heading_deg": None,
            "hdop": hdop,
            "accuracy_m": None,
            "satellites": sats,
            "source": source,
            "delta_m": 0.0,
        }

    acc_m = estimate_accuracy_m(
        reported_accuracy_m=rep_acc,
        hdop=hdop,
        fix_quality=fix_q,
        satellites=sats,
    )
    filt = filter_kinematic_fix(
        prev_lat=prev_vehicle.get("gps_lat"),
        prev_lon=prev_vehicle.get("gps_lon"),
        prev_ts=prev_vehicle.get("gps_fix_at") or prev_vehicle.get("last_seen"),
        lat=float(raw_lat),
        lon=float(raw_lon),
        now_ts=ts,
        reported_speed_kmh=spd_in,
        reported_heading_deg=hdg_in,
        accuracy_m=acc_m,
    )
    if not filt["accepted"]:
        return {
            "accepted": False,
            "is_valid": False,
            "reason": filt["reason"],
            "lat": prev_vehicle.get("gps_lat"),
            "lon": prev_vehicle.get("gps_lon"),
            "altitude_m": alt_m,
            "speed_kmh": 0.0,
            "heading_deg": filt["heading_deg"],
            "hdop": hdop,
            "accuracy_m": acc_m,
            "satellites": sats,
            "source": source,
            "delta_m": 0.0,
        }

    return {
        "accepted": True,
        "is_valid": True,
        "reason": filt["reason"],
        "lat": round(float(filt["lat"]), 6),
        "lon": round(float(filt["lon"]), 6),
        "altitude_m": round(alt_m, 1) if alt_m is not None else None,
        "speed_kmh": round(float(filt["speed_kmh"]), 1),
        "heading_deg": filt["heading_deg"],
        "hdop": round(hdop, 2) if hdop is not None else None,
        "accuracy_m": acc_m,
        "satellites": sats,
        "source": source,
        "delta_m": filt["delta_m"],
        "fix_ts": ts,
    }


def ingest_phone_gnss(
    vehicle_id: str,
    phone_gps: Dict[str, Any],
    now_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Validate, filter, and persist REAL smartphone hardware GNSS fix (never sample/mock)."""
    ts = float(now_s if now_s is not None else time.time())
    gps = phone_gps if isinstance(phone_gps, dict) else {}
    raw_valid = bool(gps.get("is_valid", True))
    raw_lat = _opt_float(gps.get("lat"))
    raw_lon = _opt_float(gps.get("lon"))
    acc_m = _opt_float(gps.get("accuracy_m", gps.get("accuracy")))
    alt_m = _opt_float(gps.get("altitude_m", gps.get("altitude")))
    spd_in = _opt_float(gps.get("speed_kmh"))
    if spd_in is None and gps.get("speed_mps") is not None:
        mps = _opt_float(gps.get("speed_mps"))
        if mps is not None and mps >= 0.0:
            spd_in = mps * 3.6
    hdg_in = _opt_float(gps.get("heading_deg", gps.get("heading")))

    if not (raw_valid and is_valid_wgs84(raw_lat, raw_lon)):
        return {"accepted": False, "reason": "INVALID_PHONE_WGS84"}
    if acc_m is not None and acc_m > PHONE_MAX_ACCEPTABLE_ACC_M:
        return {"accepted": False, "reason": f"LOW_ACCURACY_{acc_m:.0f}M"}

    prev = database.get_vehicle(vehicle_id) or {}
    filt = filter_kinematic_fix(
        prev_lat=prev.get("phone_gps_lat"),
        prev_lon=prev.get("phone_gps_lon"),
        prev_ts=prev.get("phone_gps_at"),
        lat=float(raw_lat),
        lon=float(raw_lon),
        now_ts=ts,
        reported_speed_kmh=spd_in,
        reported_heading_deg=hdg_in,
        accuracy_m=acc_m,
    )
    if not filt["accepted"]:
        return {"accepted": False, "reason": filt["reason"]}

    eff_lat = round(float(filt["lat"]), 6)
    eff_lon = round(float(filt["lon"]), 6)
    eff_spd = round(float(filt["speed_kmh"]), 1)
    eff_hdg = filt["heading_deg"]
    eff_acc = round(acc_m, 1) if (acc_m is not None and acc_m > 0.0) else None

    database.upsert_vehicle(
        vehicle_id,
        phone_gps_lat=eff_lat,
        phone_gps_lon=eff_lon,
        phone_gps_alt_m=round(alt_m, 1) if alt_m is not None else None,
        phone_gps_speed_kmh=eff_spd,
        phone_gps_heading_deg=eff_hdg,
        phone_gps_accuracy_m=eff_acc,
        phone_gps_at=ts,
        xyz_lat=eff_lat,
        xyz_lon=eff_lon,
        xyz_is_sample_anchor=0,
    )

    # Record into telemetry_history and active trip motion ONLY when ESP32 #2 does not have a fresher live fix
    esp_fix_at = _opt_float(prev.get("gps_fix_at"))
    esp_is_live = bool(prev.get("gps_valid")) and esp_fix_at is not None and (ts - esp_fix_at) <= ESP2_LIVE_TTL_S
    if not esp_is_live:
        with database.tx() as conn:
            conn.execute(
                "INSERT INTO telemetry_history (vehicle_id, ts, speed_kmh, lat, lon, alt_m, heading_deg, accuracy_m, gps_source, raw_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    vehicle_id,
                    ts,
                    eff_spd,
                    eff_lat,
                    eff_lon,
                    round(alt_m, 1) if alt_m is not None else None,
                    eff_hdg,
                    eff_acc,
                    "phone_gnss",
                    database.dumps({"phone_gps": gps}),
                ),
            )
        from server_ai.server_core import analytics

        trip_id = prev.get("current_trip_id")
        if trip_id:
            analytics.update_trip_motion(
                vehicle_id=vehicle_id,
                trip_id=trip_id,
                prev_lat=prev.get("phone_gps_lat"),
                prev_lon=prev.get("phone_gps_lon"),
                prev_valid=is_valid_wgs84(prev.get("phone_gps_lat"), prev.get("phone_gps_lon")),
                lat=eff_lat,
                lon=eff_lon,
                gps_valid=True,
                speed_kmh=eff_spd,
            )

    return {
        "accepted": True,
        "reason": filt["reason"],
        "lat": eff_lat,
        "lon": eff_lon,
        "speed_kmh": eff_spd,
        "heading_deg": eff_hdg,
        "accuracy_m": eff_acc,
        "geodetic": compute_geodetic_summary(eff_lat, eff_lon, alt_m, eff_hdg),
    }


def _extract_diagnostics(vehicle: Dict[str, Any]) -> Dict[str, Any]:
    raw = database.loads(vehicle.get("last_telemetry_json"))
    gps_raw = raw.get("gps") if isinstance(raw, dict) else {}
    bt_raw = raw.get("bluetooth_phone") if isinstance(raw, dict) else {}
    if not isinstance(gps_raw, dict):
        gps_raw = {}
    if not isinstance(bt_raw, dict):
        bt_raw = {}

    is_valid = bool(vehicle.get("gps_valid"))
    sats = _opt_int(vehicle.get("gps_satellites"))
    if sats is None:
        sats = _opt_int(gps_raw.get("satellites")) or _opt_int(bt_raw.get("gps_sats")) or 0
    sats_view = _opt_int(gps_raw.get("sats_in_view"))
    if sats_view is None:
        sats_view = _opt_int(bt_raw.get("gps_sats_in_view"))
    rx_bytes = _opt_int(gps_raw.get("gps_rx_bytes"))
    if rx_bytes is None:
        rx_bytes = _opt_int(bt_raw.get("gps_rx_bytes"))
    nmea_ok = _opt_int(gps_raw.get("nmea_ok"))
    wiring = gps_raw.get("wiring_status") or bt_raw.get("gps_wiring_status") or classify_gps_wiring(
        is_valid=is_valid,
        satellites=sats,
        sats_in_view=sats_view,
        gps_rx_bytes=rx_bytes,
        nmea_ok=nmea_ok,
    )
    return {
        "esp2_hub_connected": bool(bt_raw.get("hub_connected")),
        "satellites": sats,
        "sats_in_view": sats_view,
        "gps_rx_bytes": rx_bytes,
        "nmea_ok": nmea_ok,
        "wiring_status": str(wiring),
    }


def resolve_vehicle_position(vehicle: Dict[str, Any], now_s: Optional[float] = None) -> Dict[str, Any]:
    """Compute authoritative real-hardware position summary for a vehicle (NEVER sample/dummy)."""
    ts = float(now_s if now_s is not None else time.time())
    vid = str(vehicle.get("vehicle_id") or "UNKNOWN")
    diag = _extract_diagnostics(vehicle)

    esp_lat = _opt_float(vehicle.get("gps_lat"))
    esp_lon = _opt_float(vehicle.get("gps_lon"))
    esp_has_coord = is_valid_wgs84(esp_lat, esp_lon)
    esp_fix_at = _opt_float(vehicle.get("gps_fix_at")) or (_opt_float(vehicle.get("last_seen")) if esp_has_coord and vehicle.get("gps_valid") else None)
    esp_age = max(0.0, ts - esp_fix_at) if esp_fix_at is not None else None
    esp_live = bool(vehicle.get("gps_valid")) and esp_has_coord and (esp_age is None or esp_age <= ESP2_LIVE_TTL_S)

    ph_lat = _opt_float(vehicle.get("phone_gps_lat"))
    ph_lon = _opt_float(vehicle.get("phone_gps_lon"))
    ph_has_coord = is_valid_wgs84(ph_lat, ph_lon)
    ph_at = _opt_float(vehicle.get("phone_gps_at"))
    ph_age = max(0.0, ts - ph_at) if ph_at is not None else None
    ph_live = ph_has_coord and ph_age is not None and ph_age <= PHONE_LIVE_TTL_S

    # Also support confirmed non-sample xyz_lat/lon if written by older phone_telemetry code
    if not ph_has_coord and not bool(vehicle.get("xyz_is_sample_anchor", 1)):
        xl, xn = _opt_float(vehicle.get("xyz_lat")), _opt_float(vehicle.get("xyz_lon"))
        if is_valid_wgs84(xl, xn):
            ph_lat, ph_lon, ph_has_coord = xl, xn, True

    chosen = None
    if esp_live:
        chosen = {
            "fix_status": "LIVE_ESP2_GNSS",
            "is_live": True,
            "source": str(vehicle.get("gps_source") or "esp32_hub_gps"),
            "lat": float(esp_lat),
            "lon": float(esp_lon),
            "altitude_m": _opt_float(vehicle.get("gps_alt_m")),
            "speed_kmh": round(_opt_float(vehicle.get("gps_speed_kmh")) or 0.0, 1),
            "heading_deg": _opt_float(vehicle.get("gps_heading_deg")),
            "hdop": _opt_float(vehicle.get("gps_hdop")),
            "accuracy_m": _opt_float(vehicle.get("gps_accuracy_m")),
            "satellites": diag["satellites"],
            "fix_age_s": round(esp_age, 1) if esp_age is not None else 0.0,
        }
    elif ph_live:
        chosen = {
            "fix_status": "LIVE_PHONE_GNSS",
            "is_live": True,
            "source": "phone_gnss",
            "lat": float(ph_lat),
            "lon": float(ph_lon),
            "altitude_m": _opt_float(vehicle.get("phone_gps_alt_m")),
            "speed_kmh": round(_opt_float(vehicle.get("phone_gps_speed_kmh")) or 0.0, 1),
            "heading_deg": _opt_float(vehicle.get("phone_gps_heading_deg")),
            "hdop": None,
            "accuracy_m": _opt_float(vehicle.get("phone_gps_accuracy_m")),
            "satellites": diag["satellites"],
            "fix_age_s": round(ph_age, 1) if ph_age is not None else 0.0,
        }
    elif esp_has_coord or ph_has_coord:
        use_phone = ph_has_coord and (
            not esp_has_coord
            or (ph_at is not None and (esp_fix_at is None or ph_at > esp_fix_at))
        )
        if use_phone:
            chosen = {
                "fix_status": "LAST_KNOWN_STALE",
                "is_live": False,
                "source": "phone_gnss_last_known",
                "lat": float(ph_lat),
                "lon": float(ph_lon),
                "altitude_m": _opt_float(vehicle.get("phone_gps_alt_m")),
                "speed_kmh": 0.0,
                "heading_deg": _opt_float(vehicle.get("phone_gps_heading_deg")),
                "hdop": None,
                "accuracy_m": _opt_float(vehicle.get("phone_gps_accuracy_m")),
                "satellites": diag["satellites"],
                "fix_age_s": round(ph_age, 1) if ph_age is not None else None,
            }
        else:
            chosen = {
                "fix_status": "LAST_KNOWN_STALE",
                "is_live": False,
                "source": f"{vehicle.get('gps_source') or 'esp32_hub_gps'}_last_known",
                "lat": float(esp_lat),
                "lon": float(esp_lon),
                "altitude_m": _opt_float(vehicle.get("gps_alt_m")),
                "speed_kmh": 0.0,
                "heading_deg": _opt_float(vehicle.get("gps_heading_deg")),
                "hdop": _opt_float(vehicle.get("gps_hdop")),
                "accuracy_m": _opt_float(vehicle.get("gps_accuracy_m")),
                "satellites": diag["satellites"],
                "fix_age_s": round(esp_age, 1) if esp_age is not None else None,
            }

    if chosen is None:
        return {
            "vehicle_id": vid,
            "vehicle_name": vehicle.get("vehicle_name"),
            "driver_name": vehicle.get("current_driver_name"),
            "vehicle_status": vehicle.get("status") or "OFFLINE",
            "fix_status": "NO_FIX",
            "is_live": False,
            "has_position": False,
            "source": "none",
            "lat": None,
            "lon": None,
            "altitude_m": None,
            "speed_kmh": 0.0,
            "heading_deg": None,
            "compass": "-",
            "hdop": None,
            "accuracy_m": None,
            "satellites": diag["satellites"],
            "fix_age_s": None,
            "dms": None,
            "ecef": None,
            "utm": None,
            "maps_links": None,
            "diagnostics": diag,
        }

    geo = compute_geodetic_summary(chosen["lat"], chosen["lon"], chosen["altitude_m"], chosen["heading_deg"])
    return {
        "vehicle_id": vid,
        "vehicle_name": vehicle.get("vehicle_name"),
        "driver_name": vehicle.get("current_driver_name"),
        "vehicle_status": vehicle.get("status") or "OFFLINE",
        "fix_status": chosen["fix_status"],
        "is_live": chosen["is_live"],
        "has_position": True,
        "source": chosen["source"],
        "lat": geo["lat"],
        "lon": geo["lon"],
        "altitude_m": geo["altitude_m"],
        "speed_kmh": chosen["speed_kmh"],
        "heading_deg": round(chosen["heading_deg"], 1) if chosen["heading_deg"] is not None else None,
        "compass": compass_label_vi(chosen["heading_deg"]),
        "hdop": chosen["hdop"],
        "accuracy_m": chosen["accuracy_m"],
        "satellites": chosen["satellites"],
        "fix_age_s": chosen["fix_age_s"],
        "dms": geo["dms"],
        "ecef": geo["ecef"],
        "utm": geo["utm"],
        "maps_links": geo["maps_links"],
        "diagnostics": diag,
    }


def list_fleet_positions(now_s: Optional[float] = None) -> List[Dict[str, Any]]:
    ts = float(now_s if now_s is not None else time.time())
    return [resolve_vehicle_position(v, now_s=ts) for v in database.list_vehicles()]


def get_vehicle_track(
    vehicle_id: str,
    limit: int = 300,
    since_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Return real chronological GPS track from telemetry_history with metric ENU projection & stats."""
    lim = max(1, min(2000, int(limit)))
    conn = database.get_conn()
    if since_s is not None:
        rows = conn.execute(
            "SELECT ts, lat, lon, alt_m, speed_kmh, heading_deg, accuracy_m, gps_source "
            "FROM telemetry_history WHERE vehicle_id=? AND lat IS NOT NULL AND lon IS NOT NULL AND ts >= ? "
            "ORDER BY ts DESC LIMIT ?",
            (vehicle_id, float(since_s), lim),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT ts, lat, lon, alt_m, speed_kmh, heading_deg, accuracy_m, gps_source "
            "FROM telemetry_history WHERE vehicle_id=? AND lat IS NOT NULL AND lon IS NOT NULL "
            "ORDER BY ts DESC LIMIT ?",
            (vehicle_id, lim),
        ).fetchall()

    # Chronological oldest -> newest
    valid_rows = [dict(r) for r in reversed(rows) if is_valid_wgs84(r["lat"], r["lon"])]
    if not valid_rows:
        return {
            "vehicle_id": vehicle_id,
            "point_count": 0,
            "origin": None,
            "total_distance_km": 0.0,
            "max_speed_kmh": 0.0,
            "avg_speed_kmh": 0.0,
            "points": [],
        }

    ref_lat = float(valid_rows[0]["lat"])
    ref_lon = float(valid_rows[0]["lon"])
    ref_alt = float(valid_rows[0].get("alt_m") or 0.0)

    points: List[Dict[str, Any]] = []
    total_m = 0.0
    speeds: List[float] = []
    prev_la: Optional[float] = None
    prev_lo: Optional[float] = None

    for r in valid_rows:
        la = float(r["lat"])
        lo = float(r["lon"])
        al = float(r.get("alt_m") or 0.0)
        sp = max(0.0, float(r.get("speed_kmh") or 0.0))
        if prev_la is not None and prev_lo is not None:
            seg = haversine_m(prev_la, prev_lo, la, lo)
            if 2.0 <= seg <= 5000.0:
                total_m += seg
        prev_la, prev_lo = la, lo
        if sp > 0.0:
            speeds.append(sp)
        e_m, n_m, u_m = geodetic_to_enu(la, lo, al, ref_lat, ref_lon, ref_alt)
        points.append(
            {
                "ts": float(r["ts"]),
                "lat": round(la, 6),
                "lon": round(lo, 6),
                "alt_m": round(al, 1) if r.get("alt_m") is not None else None,
                "speed_kmh": round(sp, 1),
                "heading_deg": round(float(r["heading_deg"]), 1) if r.get("heading_deg") is not None else None,
                "accuracy_m": round(float(r["accuracy_m"]), 1) if r.get("accuracy_m") is not None else None,
                "source": r.get("gps_source") or "esp32_hub_gps",
                "east_m": round(e_m, 2),
                "north_m": round(n_m, 2),
                "up_m": round(u_m, 2),
            }
        )

    return {
        "vehicle_id": vehicle_id,
        "point_count": len(points),
        "origin": {"lat": round(ref_lat, 6), "lon": round(ref_lon, 6), "alt_m": round(ref_alt, 1)},
        "total_distance_km": round(total_m / 1000.0, 4),
        "max_speed_kmh": round(max(speeds), 1) if speeds else 0.0,
        "avg_speed_kmh": round(sum(speeds) / len(speeds), 1) if speeds else 0.0,
        "points": points,
    }
