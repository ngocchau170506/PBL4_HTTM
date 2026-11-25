"""Read-only camera network route inspector & RTSP URL builder (ZERO sudo, ZERO nmcli!)."""
from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path
from typing import Dict, List, Optional


def inspect_eth_camera_route_readonly(camera_ip: str = "192.168.1.210", iface: str = "eth0") -> Dict[str, object]:
    """Strictly READ-ONLY network diagnostic (preserves netplan-eth0 & 192.168.137.7 rescue IP)."""
    carrier_path = Path(f"/sys/class/net/{iface}/carrier")
    carrier: Optional[int] = None
    if carrier_path.exists():
        try:
            carrier = int(carrier_path.read_text().strip())
        except Exception:
            carrier = None

    route_line = ""
    try:
        route_line = subprocess.check_output(
            ["ip", "route", "show", f"{camera_ip}/32"], timeout=1.0, text=True
        ).strip()
    except Exception:
        route_line = ""

    rtsp_reachable = False
    try:
        with socket.create_connection((camera_ip, 554), timeout=0.5):
            rtsp_reachable = True
    except OSError:
        rtsp_reachable = False

    return {
        "iface": iface,
        "carrier": carrier,
        "camera_ip": camera_ip,
        "route_present": bool(route_line),
        "route_line": route_line,
        "rtsp_port_554_open": rtsp_reachable,
    }


def build_rtsp_candidates(
    camera_uri: str,
    camera_ip: str,
    password: str,
    prefer_substream: bool = True,
) -> List[str]:
    if camera_uri:
        return [camera_uri]
    if not camera_ip:
        return []
    pw_raw = (password or "").strip()
    from urllib.parse import quote
    pw_enc = quote(pw_raw, safe="")
    sub = [
        f"rtsp://{camera_ip}:554/user=admin_password={pw_raw}_channel=1_stream=1&protocol=unicast&onvif=ture.sdp?real_stream",
        f"rtsp://admin:{pw_enc}@{camera_ip}:554/ch01/1",
    ]
    main = [
        f"rtsp://{camera_ip}:554/user=admin_password={pw_raw}_channel=1_stream=0&protocol=unicast&onvif=ture.sdp?real_stream",
        f"rtsp://admin:{pw_enc}@{camera_ip}:554/ch01/0",
    ]
    return (sub + main) if prefer_substream else (main + sub)


def configure_opencv_ffmpeg_env(ffmpeg_threads: int = 1) -> None:
    # FFmpeg 5+/6+ removed 'stimeout' in favor of 'timeout'/'rw_timeout' (fixes BUG-06 infinite RTSP hang!)
    os.environ.setdefault(
        "OPENCV_FFMPEG_CAPTURE_OPTIONS",
        f"rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|timeout;2500000|rw_timeout;2500000|threads;{max(1, int(ffmpeg_threads))}",
    )
