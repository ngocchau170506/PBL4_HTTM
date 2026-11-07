"""Đường dẫn truyền viễn thông hai chiều giữa Máy chủ cục bộ (Pi Edge) và Server đối tác (FEAT-102).

- Quản lý truyền tải dòng dữ liệu cảm biến đa nguồn (esp, mq135, gps, loa, pi).
- Đệm ngoại tuyến thông minh (Offline Spooling): khi Server đối tác chưa bật (offline),
  tự động lưu tạm các gói tin vào hàng đợi FIFO giới hạn RAM, không làm gián đoạn luồng.
- Bắt tay tự động (Auto-Handshake & Drain): ngay khi Server đối tác run (online),
  tự động kết nối, xả sạch hàng đợi đệm và tiếp tục stream thời gian thực.
- Hỗ trợ kênh phản hồi hai chiều (Downlink): tiếp nhận tọa độ XYZ do máy chủ đối tác
  gửi ngược xuống để nạp trực tiếp vào hệ thống định vị của Pi.
- Chỉ dùng thư viện chuẩn của Python; không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from fd_response.telemetry_multiplexer import MultiplexedTelemetryPayload


def _now_ms() -> int:
    return int(time.time() * 1000)


StreamTransportFn = Callable[[MultiplexedTelemetryPayload, int], Tuple[bool, str, Optional[Dict[str, Any]]]]


def default_http_stream_transport(endpoint_url: str) -> StreamTransportFn:
    """Tạo hàm truyền HTTP POST dòng Telemetry bằng urllib.request chuẩn."""
    url = endpoint_url.strip()

    def _send(payload: MultiplexedTelemetryPayload, timeout_ms: int) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        body = payload.to_json().encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "Pi-TelemetryStreamer/1.0",
                "X-Device-Id": payload.device_id,
            },
            method="POST",
        )
        timeout_s = max(0.05, float(timeout_ms) / 1000.0)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                status = getattr(resp, "status", 200)
                raw_data = resp.read().decode("utf-8", errors="replace").strip()
                if 200 <= int(status) < 300:
                    downlink: Optional[Dict[str, Any]] = None
                    try:
                        parsed = json.loads(raw_data)
                        if isinstance(parsed, dict) and "downlink_xyz" in parsed:
                            downlink = parsed["downlink_xyz"]
                    except (json.JSONDecodeError, ValueError) as exc:
                        _ = exc
                    return True, raw_data or f"HTTP_{status}", downlink
                return False, f"HTTP_{status}:{raw_data}", None
        except (TimeoutError, urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            return False, f"ERR_CONNECT:{exc}", None

    return _send


class TelemetryStreamer:
    """Trình quản lý streaming và đệm ngoại tuyến kết nối với Server đối tác."""

    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        transport: Optional[StreamTransportFn] = None,
        max_spool: int = 120,
        timeout_ms: int = 800,
        on_downlink_xyz: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        if endpoint_url is None:
            base_url = os.getenv("REMOTE_SERVER_URL", "http://localhost:5000").rstrip("/")
            endpoint_url = f"{base_url}/api/v1/telemetry"
        self._endpoint_url = endpoint_url
        self._transport: StreamTransportFn = (
            transport if transport is not None else default_http_stream_transport(endpoint_url)
        )
        self._max_spool = max(10, int(max_spool))
        self._timeout_ms = max(50, int(timeout_ms))
        self._on_downlink_xyz = on_downlink_xyz

        self._spool: Deque[MultiplexedTelemetryPayload] = deque(maxlen=self._max_spool)
        self._is_connected: bool = False
        self._sent_count: int = 0
        self._spooled_count: int = 0
        self._dropped_count: int = 0
        self._last_ack: Optional[str] = None
        self._last_downlink_xyz: Optional[Dict[str, Any]] = None

    @property
    def endpoint_url(self) -> str:
        return self._endpoint_url

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def spool_size(self) -> int:
        return len(self._spool)

    @property
    def sent_count(self) -> int:
        return self._sent_count

    @property
    def spooled_count(self) -> int:
        return self._spooled_count

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def last_downlink_xyz(self) -> Optional[Dict[str, Any]]:
        return self._last_downlink_xyz

    def send(
        self,
        payload: MultiplexedTelemetryPayload,
        timeout_ms: Optional[int] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Truyền 1 gói dữ liệu đa tín hiệu tới Server đối tác."""
        tout = timeout_ms if timeout_ms is not None else self._timeout_ms
        ok, msg, downlink = self._transport(payload, tout)

        if ok:
            self._is_connected = True
            self._sent_count += 1
            self._last_ack = msg
            if downlink is not None:
                self._last_downlink_xyz = downlink
                if self._on_downlink_xyz is not None:
                    self._on_downlink_xyz(downlink)
            # Tự động xả đệm khi đường truyền thông suốt
            if self._spool:
                self.flush_spool(timeout_ms=tout)
            return True, msg, downlink

        # Server đối tác chưa bật (offline): đưa vào đệm ngoại tuyến
        self._is_connected = False
        if len(self._spool) >= self._max_spool:
            self._spool.popleft()
            self._dropped_count += 1
        self._spool.append(payload)
        self._spooled_count += 1
        return False, "SPOOLED_OFFLINE", None

    def flush_spool(self, max_drain: int = 20, timeout_ms: Optional[int] = None) -> int:
        """Xả các gói tin đã lưu đệm trong thời gian Server đối tác tắt."""
        if not self._spool:
            return 0
        tout = timeout_ms if timeout_ms is not None else self._timeout_ms
        drained = 0
        while self._spool and drained < max_drain:
            item = self._spool[0]
            ok, msg, downlink = self._transport(item, tout)
            if not ok:
                self._is_connected = False
                break
            self._spool.popleft()
            self._sent_count += 1
            self._is_connected = True
            drained += 1
            if downlink is not None:
                self._last_downlink_xyz = downlink
                if self._on_downlink_xyz is not None:
                    self._on_downlink_xyz(downlink)
        return drained

    def clear_spool(self) -> int:
        """Dọn dẹp sạch hàng đợi đệm ngoại tuyến."""
        count = len(self._spool)
        self._spool.clear()
        return count
