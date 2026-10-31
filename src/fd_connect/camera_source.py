"""Nguồn tiếp nhận Camera — phát gói RawPacket hoặc ErrorEvent (FEAT-010 & FEAT-060).

- Tuân thủ hợp đồng [C-CONNECT] trong features/component.txt và src/contracts/logic.py.
- Nguồn cam ngoại chính: Camera IP Vantech kết nối qua dây cáp mạng LAN (giao thức RTSP TCP, 192.168.1.210).
- Thiết lập thông số FFMPEG tối ưu giảm trễ qua mạng LAN: rtsp_transport=tcp, fflags=nobuffer, low_delay.
- Kiểm tra kết nối TCP nhanh tới camera Vantech LAN (chống treo socket 30s khi rút cáp mạng LAN).
- Hỗ trợ chế độ bắt buộc cam ngoại LAN (require_hardware=True) chuẩn bị triển khai thực chiến trên Raspberry Pi.
- Hỗ trợ kiến trúc Hướng 2: Bounded Frame Cache lưu trữ ảnh thật theo frame_id cho AI suy diễn.
- Hỗ trợ chế độ Fallback Mock tự động khi chạy kiểm thử tự động không có kết nối dây mạng LAN tới camera Vantech.
- Không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

from collections import deque
import logging
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import time
from typing import Any, Callable, Deque, Dict, Optional, Tuple, Union
from urllib.parse import urlparse

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId, RawPacket

try:
    import cv2
    import numpy as np
    _CV2_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    _CV2_AVAILABLE = False

FRAME_MAGIC = b"FDRW"
HEADER_STRUCT = struct.Struct(">4sHHI B")
logger = logging.getLogger("fd_connect.camera")


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
    """Tạo chuỗi tham số FFMPEG giảm trễ bộ đệm tối đa khi mở luồng RTSP camera Vantech qua mạng LAN."""
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


def check_ip_camera_reachable(uri: str, timeout_s: float = 0.5) -> bool:
    """Kiểm tra nhanh cổng TCP tới camera IP Vantech qua dây mạng LAN trước khi mở luồng."""
    try:
        parsed = urlparse(uri)
        host = parsed.hostname
        port = parsed.port or 554
        if not host:
            return False
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except Exception as sock_err:
        logger.debug("Không thể kết nối TCP tới camera Vantech tại %s: %s", uri, sock_err)
        return False


def get_external_capture_backend() -> int:
    """Xác định backend tối ưu cho webcam ngoại trên Linux/Raspberry Pi (V4L2) hoặc Windows (DSHOW)."""
    if not _CV2_AVAILABLE or cv2 is None:
        return 0
    if sys.platform.startswith("linux"):
        return getattr(cv2, "CAP_V4L2", 0)
    if sys.platform.startswith("win"):
        return getattr(cv2, "CAP_DSHOW", 0)
    return 0


def open_external_camera_capture(index: int, auto_scan: bool = False) -> Optional[Any]:
    """Mở thiết bị camera phụ (USB webcam nếu có); tự động thử backend phù hợp."""
    if not _CV2_AVAILABLE or cv2 is None:
        return None

    backend = get_external_capture_backend()
    candidate_indices = [index]
    if auto_scan:
        candidate_indices.extend([i for i in range(4) if i != index])

    for idx in candidate_indices:
        try:
            cap = cv2.VideoCapture(idx, backend) if backend != 0 else cv2.VideoCapture(idx)
            if cap.isOpened():
                return cap
            cap.release()
        except Exception as open_err:
            logger.debug("Không mở được camera tại index %d: %s", idx, open_err)
    return None


class CameraSource:
    """Thực thi NodeContract cho node connect.camera (FEAT-010 & FEAT-060)."""

    node_id: NodeId = "connect.camera"

    # Bộ nhớ đệm toàn cục (Hướng 2) hỗ trợ truy vấn frame theo frame_id
    _global_frame_cache: Dict[int, Any] = {}
    _global_cache_keys: Deque[int] = deque()
    _global_lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        source: str = "webcam",
        index: int = 0,
        uri: Optional[str] = None,
        fps: int = 25,
        width: int = 640,
        height: int = 480,
        timeout_ms: int = 1000,
        frame_provider: Optional[Callable[[int], Optional[bytes]]] = None,
        fallback_mock: bool = True,
        cache_capacity: int = 100,
        require_hardware: bool = False,
    ) -> None:
        actual_source = "mock" if frame_provider is not None else source
        self._source = actual_source
        self._index = index
        self._uri = uri
        self._fps = fps
        self._width = width
        self._height = height
        self._timeout_ms = timeout_ms
        self._frame_provider = frame_provider
        self._require_hardware = bool(
            require_hardware or (os.getenv("FD_REQUIRE_CAMERA", "0") in ("1", "true", "TRUE"))
        )
        self._fallback_mock = False if self._require_hardware else fallback_mock
        self._cache_capacity = max(10, int(cache_capacity))

        self._is_open: bool = False
        self._is_mock: bool = (actual_source == "mock" or frame_provider is not None)
        self._forced_lost: bool = False
        self._forced_timeout: bool = False
        self._health: Health = "DOWN"
        self._seq: int = 0
        self._window_count: int = 0
        self._window_start_s: float = time.monotonic()
        self._measured_fps: float = 0.0

        # Quản lý luồng nền OpenCV capture
        self._capture: Any = None
        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()
        self._condition: threading.Condition = threading.Condition()
        self._latest_real_frame: Any = None
        self._latest_frame_ts: float = 0.0
        self._camera_disconnected: bool = False

        # Bộ đệm instance (Hướng 2)
        self._frame_cache: Dict[int, Any] = {}
        self._cache_keys: Deque[int] = deque()
        self._cache_lock: threading.Lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self._is_open and not self._forced_lost

    @property
    def is_mock(self) -> bool:
        return self._is_mock

    @property
    def require_hardware(self) -> bool:
        return self._require_hardware

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
        """Mở kết nối nguồn camera Vantech LAN hoặc thiết bị camera. Trả về None nếu thành công."""
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
        self._camera_disconnected = False
        self._stop_event.clear()

        # Nguồn mock hoặc có frame_provider riêng
        if self._source == "mock" or self._frame_provider is not None:
            if self._require_hardware:
                self._is_open = False
                self._health = "DOWN"
                return self._make_error(
                    trace_id,
                    "E_CONN_OPEN",
                    "Cấu hình bắt buộc cam ngoại (require_hardware=True), không được sử dụng nguồn mock",
                    False,
                )
            self._is_mock = True
            self._is_open = True
            self._health = "OK"
            self._window_count = 0
            self._window_start_s = time.monotonic()
            return None

        # Tự động phân giải mật khẩu từ biến môi trường hoặc .env nếu có
        resolved_uri = self._uri or ""
        if "${FD_CAMERA_PASSWORD}" in resolved_uri:
            pwd = os.getenv("FD_CAMERA_PASSWORD", "")
            if not pwd:
                env_file = Path(__file__).resolve().parents[2] / ".env"
                if env_file.is_file():
                    try:
                        for line in env_file.read_text(encoding="utf-8").splitlines():
                            stripped = line.strip()
                            if stripped.startswith("FD_CAMERA_PASSWORD="):
                                pwd = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                                break
                    except Exception as env_err:
                        logger.debug("Không đọc được file .env: %s", env_err)
            if pwd:
                resolved_uri = resolved_uri.replace("${FD_CAMERA_PASSWORD}", pwd)

        # Nếu URI chứa biến môi trường chưa gán và đang ở chế độ mock fallback
        if self._source in ("video", "rtsp") and "${" in resolved_uri:
            if self._fallback_mock:
                logger.warning("URI camera Vantech chứa biến môi trường chưa gán (%s), fallback sang mock", self._uri)
                self._is_mock = True
                self._is_open = True
                self._health = "OK"
                self._window_count = 0
                self._window_start_s = time.monotonic()
                return None
            self._is_open = False
            self._health = "DOWN"
            return self._make_error(
                trace_id,
                "E_CONN_OPEN",
                f"Bắt buộc kết nối cam ngoại Vantech qua LAN nhưng chưa cung cấp mật khẩu: {self._uri}",
                False,
            )

        # Mở camera thật qua OpenCV
        if not _CV2_AVAILABLE:
            if self._fallback_mock:
                logger.warning("OpenCV chưa cài đặt, tự động kích hoạt fallback mock cho CameraSource")
                self._is_mock = True
                self._is_open = True
                self._health = "OK"
                self._window_count = 0
                self._window_start_s = time.monotonic()
                return None
            self._is_open = False
            self._health = "DOWN"
            return self._make_error(
                trace_id,
                "E_CONN_OPEN",
                "Thư viện OpenCV (cv2) chưa sẵn sàng để mở nguồn camera thật",
                False,
            )

        target = resolved_uri if self._source in ("video", "rtsp") else self._index
        try:
            if self._source == "rtsp":
                # Kiểm tra nhanh cổng TCP của camera Vantech qua cáp mạng LAN để tránh treo 30s
                if not check_ip_camera_reachable(target, timeout_s=0.5):
                    if self._fallback_mock:
                        logger.warning("Camera Vantech LAN không phản hồi TCP (%s), fallback sang mock", target)
                        self._is_mock = True
                        self._is_open = True
                        self._health = "OK"
                        self._window_count = 0
                        self._window_start_s = time.monotonic()
                        return None
                    self._is_open = False
                    self._health = "DOWN"
                    return self._make_error(
                        trace_id,
                        "E_CONN_OPEN",
                        f"Bắt buộc kết nối cam ngoại Vantech LAN nhưng không thể kết nối tới {target}",
                        False,
                    )
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = build_rtsp_low_latency_options(2000000)
                cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG)
            elif self._source == "webcam":
                cap = open_external_camera_capture(self._index)
            else:
                cap = cv2.VideoCapture(target)

            if cap is None or not cap.isOpened():
                if self._fallback_mock:
                    logger.warning("Không thể kết nối camera Vantech LAN (%s), fallback sang mock", target)
                    self._is_mock = True
                    self._is_open = True
                    self._health = "OK"
                    self._window_count = 0
                    self._window_start_s = time.monotonic()
                    return None
                self._is_open = False
                self._health = "DOWN"
                return self._make_error(
                    trace_id,
                    "E_CONN_OPEN",
                    f"Bắt buộc kết nối cam ngoại Vantech LAN nhưng không thể kết nối tại {target}",
                    False,
                )

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._width))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._height))
            cap.set(cv2.CAP_PROP_FPS, float(self._fps))
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception as set_buf_err:
                logger.debug("Không thể thiết lập BUFFERSIZE=1: %s", set_buf_err)

            ok, test_frame = cap.read()
            if not ok or test_frame is None:
                cap.release()
                if self._fallback_mock:
                    logger.warning("Không đọc được khung hình thử nghiệm từ %s, fallback sang mock", target)
                    self._is_mock = True
                    self._is_open = True
                    self._health = "OK"
                    self._window_count = 0
                    self._window_start_s = time.monotonic()
                    return None
                self._is_open = False
                self._health = "DOWN"
                return self._make_error(
                    trace_id,
                    "E_CONN_OPEN",
                    f"Không đọc được khung hình từ camera Vantech LAN tại {target}",
                    False,
                )

            self._capture = cap
            self._latest_real_frame = test_frame
            self._latest_frame_ts = time.monotonic()
            self._is_mock = False
            self._is_open = True
            self._health = "OK"

            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                name="CameraSource-capture",
                daemon=True,
            )
            self._capture_thread.start()

        except Exception as exc:
            if self._fallback_mock:
                logger.warning("Lỗi ngoại lệ khi mở camera %s: %s, fallback sang mock", target, exc)
                self._is_mock = True
                self._is_open = True
                self._health = "OK"
                self._window_count = 0
                self._window_start_s = time.monotonic()
                return None
            self._is_open = False
            self._health = "DOWN"
            return self._make_error(
                trace_id,
                "E_CONN_OPEN",
                f"Ngoại lệ khi kết nối camera Vantech LAN {target}: {exc}",
                False,
            )

        self._window_count = 0
        self._window_start_s = time.monotonic()
        return None

    def _capture_loop(self) -> None:
        """Luồng ngầm thu nhận khung hình liên tục từ camera Vantech LAN và lưu giữ khung mới nhất."""
        interval_s = max(0.001, (1.0 / max(1, self._fps)) * 0.5)
        while not self._stop_event.is_set():
            if self._capture is None:
                break
            try:
                ok, frame = self._capture.read()
            except Exception as read_err:
                logger.warning("Lỗi khi đọc khung hình trong luồng nền: %s", read_err)
                ok = False
                frame = None

            if not ok or frame is None:
                with self._condition:
                    self._camera_disconnected = True
                    self._condition.notify_all()
                break

            with self._condition:
                self._latest_real_frame = frame
                self._latest_frame_ts = time.monotonic()
                self._condition.notify_all()

            time.sleep(interval_s)

    def _store_in_cache(self, seq: int, frame: Any) -> None:
        """Lưu khung hình vào bộ đệm cá nhân và bộ đệm toàn cục (Hướng 2)."""
        if frame is None:
            return

        with self._cache_lock:
            if len(self._cache_keys) >= self._cache_capacity:
                oldest_key = self._cache_keys.popleft()
                self._frame_cache.pop(oldest_key, None)
            self._cache_keys.append(seq)
            self._frame_cache[seq] = frame

        with CameraSource._global_lock:
            if len(CameraSource._global_cache_keys) >= self._cache_capacity:
                oldest_g_key = CameraSource._global_cache_keys.popleft()
                CameraSource._global_frame_cache.pop(oldest_g_key, None)
            CameraSource._global_cache_keys.append(seq)
            CameraSource._global_frame_cache[seq] = frame

        # Cập nhật vào dictionary toàn cục trong sys.modules để các module khác truy xuất an toàn không cần import chéo
        shared = sys.modules.setdefault("_shared_frame_cache", {})
        shared[seq] = frame
        if len(shared) > self._cache_capacity:
            old_keys = list(shared.keys())[:-self._cache_capacity]
            for k in old_keys:
                shared.pop(k, None)

    def close(self) -> None:
        """Đóng nguồn camera an toàn và giải phóng tài nguyên luồng nền."""
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()

        if self._capture_thread is not None and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=1.0)
            self._capture_thread = None

        if self._capture is not None:
            try:
                self._capture.release()
            except Exception as rel_err:
                logger.warning("Lỗi giải phóng capture: %s", rel_err)
            self._capture = None

        self._is_open = False
        self._health = "DOWN"
        self.clear_cache()

    def simulate_disconnect(self) -> None:
        """Kích hoạt trạng thái mất kết nối nguồn để kiểm thử E_CONN_LOST."""
        self._forced_lost = True
        self._is_open = False
        self._health = "DOWN"

    def simulate_reconnect(self) -> None:
        """Kích hoạt kết nối lại nguồn camera thành công sau khi mất kết nối."""
        self._forced_lost = False
        self._is_open = True
        self._health = "OK"

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
        elif self._is_mock:
            if np is not None:
                mock_frame = np.full((self._height, self._width, 3), 128, dtype=np.uint8)
                self._store_in_cache(self._seq, mock_frame)
            payload = encode_raw_frame(
                width=self._width,
                height=self._height,
                seq=self._seq,
                luma_mean=128,
            )
        else:
            if self._camera_disconnected:
                self._is_open = False
                self._health = "DOWN"
                return self._make_error(
                    trace_id,
                    "E_CONN_LOST",
                    f"Camera Vantech LAN ({self._source}) bị ngắt kết nối đột ngột",
                    True,
                )

            with self._condition:
                frame = self._latest_real_frame
                ts = self._latest_frame_ts

            now_s = time.monotonic()
            if frame is None or (now_s - ts) * 1000.0 > self._timeout_ms:
                self._health = "DEGRADED"
                return self._make_error(
                    trace_id,
                    "E_CONN_TIMEOUT",
                    f"Quá thời gian chờ khung hình từ luồng nền ({self._timeout_ms}ms)",
                    True,
                )

            if hasattr(frame, "shape"):
                h, w = frame.shape[:2]
                if (w, h) != (self._width, self._height) and cv2 is not None:
                    frame = cv2.resize(frame, (self._width, self._height))
                luma_val = max(1, min(255, int(frame.mean())))
            else:
                luma_val = 128

            self._store_in_cache(self._seq, frame)
            payload = encode_raw_frame(
                width=self._width,
                height=self._height,
                seq=self._seq,
                luma_mean=luma_val,
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

    def get_cached_frame(self, frame_id: int) -> Optional[Any]:
        """Truy xuất khung hình đã đệm theo frame_id (Hướng 2)."""
        with self._cache_lock:
            return self._frame_cache.get(frame_id)

    def get_latest_frame(self) -> Optional[Tuple[int, Any]]:
        """Lấy khung hình mới nhất kèm seq từ bộ đệm (Hướng 2)."""
        with self._cache_lock:
            if not self._cache_keys:
                return None
            latest_id = self._cache_keys[-1]
            return latest_id, self._frame_cache.get(latest_id)

    def clear_cache(self) -> None:
        """Xóa bộ nhớ đệm khung hình của instance."""
        with self._cache_lock:
            self._frame_cache.clear()
            self._cache_keys.clear()

    @classmethod
    def get_global_frame(cls, frame_id: int) -> Optional[Any]:
        """Truy xuất khung hình từ bộ đệm tĩnh toàn cục (Hướng 2)."""
        with cls._global_lock:
            return cls._global_frame_cache.get(frame_id)

    @classmethod
    def clear_global_cache(cls) -> None:
        """Xóa bộ nhớ đệm tĩnh toàn cục."""
        with cls._global_lock:
            cls._global_frame_cache.clear()
            cls._global_cache_keys.clear()
        shared = sys.modules.get("_shared_frame_cache")
        if isinstance(shared, dict):
            shared.clear()

    def self_test(self) -> Tuple[bool, str]:
        err = self.open(trace_id="cam-selftest-open")
        if err is not None:
            return False, f"{err.code}: {err.message}"
        pkt = self.read_packet(trace_id="cam-selftest-read")
        if isinstance(pkt, ErrorEvent):
            return False, f"{pkt.code}: {pkt.message}"
        cached = self.get_cached_frame(self._seq)
        cached_status = "co_cache" if cached is not None else "khong_cache"
        return True, (
            f"CameraSource OK (source={self._source}, mock={self._is_mock}, "
            f"payload={len(pkt.payload)}B, cache={cached_status})"
        )
