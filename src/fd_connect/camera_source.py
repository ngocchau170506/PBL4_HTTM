"""Nguồn tiếp nhận Camera — phát gói RawPacket hoặc ErrorEvent (FEAT-010).

- Tuân thủ hợp đồng [C-CONNECT] trong features/component.txt và src/contracts/logic.py.
- Chỉ mở/đóng/giám sát nguồn và đóng gói RawPacket, không xử lý ảnh hay gọi AI (Luật R1).
- Không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

import struct
import time
from typing import Callable, Optional, Tuple, Union

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId, RawPacket

FRAME_MAGIC = b"FDRW"
HEADER_STRUCT = struct.Struct(">4sHHI B")


def _now_ms() -> int:
    return int(time.time() * 1000)


def encode_raw_frame(
    width: int,
    height: int,
    seq: int,
    luma_mean: int = 128,
    extra_bytes: bytes = b"\x80\x80\x80\x80",
) -> bytes:
    """Đóng gói khung dữ liệu thô kèm header nhị phân 13 byte để tầng signal kiểm chứng."""
    clamped_w = max(0, min(65535, int(width)))
    clamped_h = max(0, min(65535, int(height)))
    clamped_seq = max(0, int(seq)) & 0xFFFFFFFF
    clamped_luma = max(0, min(255, int(luma_mean)))
    header = HEADER_STRUCT.pack(FRAME_MAGIC, clamped_w, clamped_h, clamped_seq, clamped_luma)
    return header + extra_bytes


def build_rtsp_low_latency_options(timeout_us: int = 2000000) -> str:
    """Tạo chuỗi tham số FFMPEG giảm trễ bộ đệm khi mở luồng RTSP camera ngoài."""
    safe_timeout = max(100000, int(timeout_us))
    return (
        "rtsp_transport;tcp|"
        "fflags;nobuffer|"
        "flags;low_delay|"
        "max_delay;50000|"
        "probesize;32768|"
        "analyzeduration;50000|"
        f"stimeout;{safe_timeout}"
    )


class CameraSource:
    """Thực thi NodeContract cho node connect.camera."""

    node_id: NodeId = "connect.camera"

    def __init__(
        self,
        source: str = "webcam",
        index: int = 0,
        uri: Optional[str] = None,
        fps: int = 10,
        width: int = 640,
        height: int = 480,
        timeout_ms: int = 1000,
        frame_provider: Optional[Callable[[int], Optional[bytes]]] = None,
    ) -> None:
        self._source = source
        self._index = index
        self._uri = uri
        self._fps = fps
        self._width = width
        self._height = height
        self._timeout_ms = timeout_ms
        self._frame_provider = frame_provider

        self._is_open: bool = False
        self._forced_lost: bool = False
        self._forced_timeout: bool = False
        self._health: Health = "DOWN"
        self._seq: int = 0
        self._window_count: int = 0
        self._window_start_s: float = time.monotonic()
        self._measured_fps: float = 0.0

    @property
    def is_open(self) -> bool:
        return self._is_open and not self._forced_lost

    @property
    def measured_fps(self) -> float:
        return self._measured_fps

    @property
    def packet_count(self) -> int:
        return self._seq

    def health(self) -> Health:
        return self._health

    def _make_error(
        self,
        trace_id: str,
        code: ErrorCode,
        message: str,
        recoverable: bool = True,
    ) -> ErrorEvent:
        safe_trace = trace_id if trace_id else "cam-missing-trace"
        return ErrorEvent(
            trace_id=safe_trace,
            ts_ms=_now_ms(),
            source="connect.camera",
            code=code,
            node_path="connect.camera",
            err_class="source",
            message=message,
            recoverable=recoverable,
        )

    def open(self, trace_id: str = "cam-open") -> Optional[ErrorEvent]:
        """Mở kết nối nguồn camera. Trả về None nếu thành công, hoặc ErrorEvent nếu lỗi."""
        if not trace_id:
            self._health = "DOWN"
            return self._make_error("cam-no-trace", "E_CONN_OPEN", "trace_id không được rỗng", False)

        if self._fps <= 0 or self._width <= 0 or self._height <= 0:
            self._is_open = False
            self._health = "DOWN"
            return self._make_error(
                trace_id,
                "E_CONN_OPEN",
                f"Thông số camera không hợp lệ ({self._width}x{self._height}@{self._fps}fps)",
                False,
            )

        if self._source not in ("webcam", "video", "rtsp", "mock"):
            self._is_open = False
            self._health = "DOWN"
            return self._make_error(
                trace_id,
                "E_CONN_OPEN",
                f"Loại nguồn camera không được hỗ trợ: {self._source}",
                False,
            )

        if self._source in ("video", "rtsp") and not self._uri:
            self._is_open = False
            self._health = "DOWN"
            return self._make_error(
                trace_id,
                "E_CONN_OPEN",
                f"Nguồn {self._source} yêu cầu địa chỉ uri không rỗng",
                False,
            )

        self._forced_lost = False
        self._forced_timeout = False
        self._is_open = True
        self._health = "OK"
        self._window_count = 0
        self._window_start_s = time.monotonic()
        return None

    def close(self) -> None:
        """Đóng nguồn camera an toàn."""
        self._is_open = False
        self._health = "DOWN"

    def simulate_disconnect(self) -> None:
        """Kích hoạt trạng thái mất kết nối nguồn để kiểm thử E_CONN_LOST."""
        self._forced_lost = True
        self._is_open = False
        self._health = "DOWN"

    def simulate_timeout(self) -> None:
        """Kích hoạt trạng thái quá thời gian chờ khung hình để kiểm thử E_CONN_TIMEOUT."""
        self._forced_timeout = True
        self._health = "DEGRADED"

    def read_packet(self, trace_id: str) -> Union[RawPacket, ErrorEvent]:
        """Đọc 1 khung hình thô và đóng gói thành RawPacket hoặc phát ErrorEvent."""
        if not trace_id:
            self._health = "DOWN"
            return self._make_error("cam-no-trace", "E_CONN_OPEN", "trace_id không được rỗng", False)

        if self._forced_lost or not self._is_open:
            self._health = "DOWN"
            return self._make_error(
                trace_id,
                "E_CONN_LOST",
                f"Mất kết nối tới nguồn camera ({self._source})",
                True,
            )

        if self._forced_timeout:
            self._forced_timeout = False
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_CONN_TIMEOUT",
                f"Quá thời gian chờ khung hình ({self._timeout_ms}ms)",
                True,
            )

        t0_ms = _now_ms()
        self._seq += 1

        if self._frame_provider is not None:
            payload = self._frame_provider(self._seq)
            if payload is None:
                self._is_open = False
                self._health = "DOWN"
                return self._make_error(
                    trace_id,
                    "E_CONN_LOST",
                    "Nguồn cung cấp frame trả về rỗng (ngắt luồng)",
                    True,
                )
        else:
            payload = encode_raw_frame(
                width=self._width,
                height=self._height,
                seq=self._seq,
                luma_mean=128,
            )

        elapsed_ms = _now_ms() - t0_ms
        if elapsed_ms > self._timeout_ms:
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_CONN_TIMEOUT",
                f"Đọc frame mất {elapsed_ms}ms vượt ngưỡng {self._timeout_ms}ms",
                True,
            )

        self._window_count += 1
        now_s = time.monotonic()
        span_s = now_s - self._window_start_s
        if span_s >= 0.5:
            self._measured_fps = round(self._window_count / span_s, 2)
            self._window_count = 0
            self._window_start_s = now_s

        self._health = "OK"
        return RawPacket(
            trace_id=trace_id,
            ts_ms=_now_ms(),
            source="connect.camera",
            payload=payload,
            sensor_kind="camera",
        )

    def self_test(self) -> Tuple[bool, str]:
        err = self.open(trace_id="cam-selftest-open")
        if err is not None:
            return False, f"{err.code}: {err.message}"
        pkt = self.read_packet(trace_id="cam-selftest-read")
        if isinstance(pkt, ErrorEvent):
            return False, f"{pkt.code}: {pkt.message}"
        return True, f"CameraSource OK (source={self._source}, payload={len(pkt.payload)}B)"
