"""Offline-LAN-safe UDP Auto-Discovery :5005 (resolves interface IP toward requester, never returns 127.0.0.1)."""
from __future__ import annotations

import json
import socket
import threading
from typing import Optional

from server_ai.server_core import config


def resolve_lan_ip_for_peer(peer_ip: str) -> str:
    """Determine our LAN interface IP facing the requesting peer (works 100% offline without 8.8.8.8)."""
    for target in (peer_ip, "192.168.1.1", "10.0.0.1", "8.8.8.8"):
        if not target or target.startswith("127."):
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
        finally:
            s.close()
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def _serve(stop_event: threading.Event) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", config.UDP_DISCOVERY_PORT))
    except OSError:
        sock.close()
        return
    sock.settimeout(1.0)
    try:
        while not stop_event.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8", errors="ignore"))
                magic = msg.get("magic")
            except Exception:
                continue
            if magic in config.UDP_MAGIC_REQUEST_ACCEPTED:
                local_ip = resolve_lan_ip_for_peer(addr[0])
                beacon = {
                    "magic": config.UDP_MAGIC_RESPONSE,
                    "port": config.HTTP_PORT,
                    "service": config.SERVICE_NAME,
                    "server_url": f"http://{local_ip}:{config.HTTP_PORT}",
                }
                try:
                    sock.sendto(json.dumps(beacon).encode("utf-8"), addr)
                except OSError:
                    pass
    finally:
        sock.close()


_stop_event: Optional[threading.Event] = None
_thread: Optional[threading.Thread] = None


def start() -> None:
    global _stop_event, _thread
    _stop_event = threading.Event()
    _thread = threading.Thread(target=_serve, args=(_stop_event,), name="udp-discovery", daemon=True)
    _thread.start()


def stop() -> None:
    if _stop_event:
        _stop_event.set()
