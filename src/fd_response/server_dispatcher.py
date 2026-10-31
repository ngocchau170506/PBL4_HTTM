"""Bộ gửi cụm frame vi phạm bất đồng bộ về máy chủ (FEAT-033).

- Không chặn luồng xử lý ảnh: enqueue() chỉ đẩy gói vào hàng đợi RAM (outbox) rồi trả về ngay.
- Chịu lỗi mạng / máy chủ quá tải: khi gửi thất bại (E_RESP_SEND) hoặc không nhận ACK (E_RESP_NO_ACK),
  gói sự kiện được chuyển sang bộ đệm tạm (offline spool) có giới hạn dung lượng để gửi bù khi mạng hồi phục.
- Có ngắt mạch (circuit breaker): khi lỗi liên tiếp đạt ngưỡng, mở mạch để chỉ thăm dò 1 gói mỗi lượt,
  không dồn tải vào máy chủ đang sập.
- Chỉ dùng thư viện chuẩn của Python; không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple, Union

from contracts.logic import (
    ErrorClass,
    ErrorCode,
    ErrorEvent,
    Health,
    NodeId,
    ViolationEvent,
)

_NODE_PATH = "response.dispatcher"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} phải là số nguyên dương")
    return value


@dataclass(frozen=True)
class DispatchPackage:
    """Gói bằng chứng vi phạm đóng gói sẵn (JSON manifest + danh sách frame_id)."""

    trace_id: str
    rule: str
    t0_ms: int
    pre_ms: int
    ts_ms: int
    frame_ids: Tuple[int, ...]
    evidence_frames: Optional[Tuple[Any, ...]] = None
    telemetry: Optional[Dict[str, Any]] = None

    def to_manifest_json(self) -> str:
        payload = {
            "trace_id": self.trace_id,
            "rule": self.rule,
            "t0_ms": self.t0_ms,
            "pre_ms": self.pre_ms,
            "ts_ms": self.ts_ms,
            "frame_ids": list(self.frame_ids),
            "frame_count": len(self.frame_ids),
        }
        if self.evidence_frames is not None:
            payload["evidence_frame_count"] = len(self.evidence_frames)
        if self.telemetry is not None:
            payload["telemetry"] = self.telemetry
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class DispatchReceipt:
    """Biên nhận khi máy chủ xác nhận (ACK) đã nhận gói vi phạm."""

    trace_id: str
    rule: str
    frame_count: int
    ack: str


TransportFn = Callable[[DispatchPackage, int], Tuple[bool, str]]


def urllib_post_transport(endpoint_url: str) -> TransportFn:
    """Tạo hàm gửi HTTP POST chuẩn bằng urllib.request (chỉ dùng khi MAIN cho phép mở mạng)."""
    if not isinstance(endpoint_url, str) or not endpoint_url.strip():
        raise ValueError("endpoint_url không được rỗng")
    url = endpoint_url.strip()

    def _send(pkg: DispatchPackage, timeout_ms: int) -> Tuple[bool, str]:
        body = pkg.to_manifest_json().encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        timeout_s = max(0.05, float(timeout_ms) / 1000.0)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                status = getattr(resp, "status", 200)
                resp_text = resp.read().decode("utf-8", errors="replace").strip()
                if 200 <= int(status) < 300:
                    return True, resp_text or f"HTTP_{status}"
                return False, f"HTTP_{status}:{resp_text}"
        except TimeoutError as exc:
            raise TimeoutError(f"Quá hạn chờ ACK từ {url}: {exc}") from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise TimeoutError(f"Quá hạn chờ ACK từ {url}: {exc.reason}") from exc
            raise OSError(f"Lỗi kết nối tới {url}: {exc}") from exc

    return _send


def _make_error(
    trace_id: str,
    code: ErrorCode,
    err_class: ErrorClass,
    message: str,
    recoverable: bool = True,
) -> ErrorEvent:
    return ErrorEvent(
        trace_id=trace_id or "NO_TRACE",
        ts_ms=_now_ms(),
        source="response",
        code=code,
        node_path=_NODE_PATH,
        err_class=err_class,
        message=message,
        recoverable=recoverable,
    )


class ServerDispatcher:
    """Thực thi NodeContract cho node response (phần gửi cụm frame vi phạm về server)."""

    node_id: NodeId = "response"

    def __init__(
        self,
        transport: TransportFn,
        max_outbox: int = 32,
        max_spool: int = 64,
        timeout_ms: int = 500,
        failure_threshold: int = 3,
        max_spool_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if not callable(transport):
            raise ValueError("transport phải là hàm gọi được")
        self._transport: TransportFn = transport
        self._max_outbox: int = _positive_int("max_outbox", max_outbox)
        self._max_spool: int = _positive_int("max_spool", max_spool)
        self._timeout_ms: int = _positive_int("timeout_ms", timeout_ms)
        self._failure_threshold: int = _positive_int("failure_threshold", failure_threshold)
        self._max_spool_bytes: int = _positive_int("max_spool_bytes", max_spool_bytes)

        self._outbox: Deque[DispatchPackage] = deque()
        self._spool: Deque[DispatchPackage] = deque()
        self._spool_bytes: int = 0
        self._health: Health = "OK"
        self._circuit_open: bool = False
        self._consecutive_failures: int = 0
        self._sent_count: int = 0
        self._dropped_count: int = 0
        self._last_ack: Optional[str] = None

    @property
    def outbox_size(self) -> int:
        return len(self._outbox)

    @property
    def spool_size(self) -> int:
        return len(self._spool)

    @property
    def spool_bytes(self) -> int:
        return self._spool_bytes

    @property
    def max_spool_bytes(self) -> int:
        return self._max_spool_bytes

    @property
    def pending_count(self) -> int:
        return len(self._outbox) + len(self._spool)

    @property
    def sent_count(self) -> int:
        return self._sent_count

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def circuit_open(self) -> bool:
        return self._circuit_open

    @property
    def last_ack(self) -> Optional[str]:
        return self._last_ack

    def health(self) -> Health:
        return self._health

    def enqueue(
        self,
        event: ViolationEvent,
        clip_frame_ids: Optional[Sequence[int]] = None,
        evidence_frames: Optional[Sequence[Any]] = None,
        telemetry: Optional[Dict[str, Any]] = None,
    ) -> Optional[ErrorEvent]:
        """Đóng gói ViolationEvent + cụm frame_id kèm ảnh bằng chứng vào hàng đợi nền."""
        if not isinstance(event, ViolationEvent):
            self._health = "DEGRADED"
            return _make_error(
                "NO_TRACE",
                "E_RESP_SEND",
                "contract",
                "Đầu vào enqueue không phải kiểu ViolationEvent",
            )
        if not event.trace_id:
            self._health = "DEGRADED"
            return _make_error(
                "NO_TRACE",
                "E_RESP_SEND",
                "contract",
                "ViolationEvent thiếu trace_id",
            )
        if not event.rule or not isinstance(event.rule, str):
            self._health = "DEGRADED"
            return _make_error(
                event.trace_id,
                "E_RESP_SEND",
                "contract",
                "ViolationEvent thiếu tên luật",
            )

        if clip_frame_ids is not None:
            merged_frames = tuple(sorted(set(event.frame_ids) | set(int(x) for x in clip_frame_ids)))
        else:
            merged_frames = tuple(event.frame_ids)

        pkg = DispatchPackage(
            trace_id=event.trace_id,
            rule=event.rule,
            t0_ms=event.t0_ms,
            pre_ms=event.pre_ms,
            ts_ms=event.ts_ms,
            frame_ids=merged_frames,
            evidence_frames=tuple(evidence_frames) if evidence_frames else None,
            telemetry=telemetry,
        )

        overrun_err: Optional[ErrorEvent] = None
        if len(self._outbox) >= self._max_outbox:
            oldest_out = self._outbox.popleft()
            overrun_err = self._push_to_spool(oldest_out, reason="outbox đầy")

        self._outbox.append(pkg)
        return overrun_err

    def _push_to_spool(
        self, pkg: DispatchPackage, reason: str
    ) -> Optional[ErrorEvent]:
        """Đưa gói vào spool ngoại tuyến; nếu spool vượt dung lượng gói hoặc byte thì dọn gói cũ nhất."""
        pkg_bytes = len(pkg.to_manifest_json().encode("utf-8"))
        overrun_err: Optional[ErrorEvent] = None

        while self._spool and (
            len(self._spool) >= self._max_spool
            or (self._spool_bytes + pkg_bytes > self._max_spool_bytes)
        ):
            evicted = self._spool.popleft()
            evicted_bytes = len(evicted.to_manifest_json().encode("utf-8"))
            self._spool_bytes = max(0, self._spool_bytes - evicted_bytes)
            self._dropped_count += 1
            self._health = "DEGRADED"
            overrun_err = _make_error(
                pkg.trace_id,
                "E_RESP_BUFFER_OVERRUN",
                "performance",
                f"Spool vượt trần ({self._max_spool} gói / {self._max_spool_bytes}B) khi {reason}: rơi gói trace_id={evicted.trace_id}",
            )

        self._spool.append(pkg)
        self._spool_bytes += pkg_bytes
        return overrun_err

    def _record_failure(
        self,
        pkg: DispatchPackage,
        code: ErrorCode,
        err_class: ErrorClass,
        detail: str,
    ) -> ErrorEvent:
        self._consecutive_failures += 1
        self._health = "DEGRADED"
        if self._consecutive_failures >= self._failure_threshold:
            self._circuit_open = True
        spool_err = self._push_to_spool(pkg, reason=code)
        if spool_err is not None:
            return spool_err
        return _make_error(pkg.trace_id, code, err_class, detail)

    def flush_one(self) -> Optional[Union[DispatchReceipt, ErrorEvent]]:
        """Gửi 1 gói ưu tiên từ spool trước rồi tới outbox."""
        if self._spool:
            pkg = self._spool.popleft()
            pkg_bytes = len(pkg.to_manifest_json().encode("utf-8"))
            self._spool_bytes = max(0, self._spool_bytes - pkg_bytes)
        elif self._outbox:
            pkg = self._outbox.popleft()
        else:
            return None

        t0_s = time.monotonic()
        try:
            ok, info = self._transport(pkg, self._timeout_ms)
        except TimeoutError as exc:
            return self._record_failure(
                pkg,
                "E_RESP_NO_ACK",
                "performance",
                f"Không nhận được ACK trong {self._timeout_ms}ms: {exc}",
            )
        except Exception as exc:
            return self._record_failure(
                pkg,
                "E_RESP_SEND",
                "source",
                f"Lỗi gửi gói vi phạm về máy chủ: {exc}",
            )
        elapsed_ms = (time.monotonic() - t0_s) * 1000.0

        if elapsed_ms > float(self._timeout_ms):
            return self._record_failure(
                pkg,
                "E_RESP_NO_ACK",
                "performance",
                f"Máy chủ phản hồi chậm {elapsed_ms:.1f}ms > {self._timeout_ms}ms",
            )

        if not ok:
            return self._record_failure(
                pkg,
                "E_RESP_NO_ACK",
                "performance",
                f"Máy chủ từ chối hoặc thiếu ACK hợp lệ: {info}",
            )

        self._consecutive_failures = 0
        self._circuit_open = False
        self._health = "OK"
        self._sent_count += 1
        self._last_ack = str(info)
        return DispatchReceipt(
            trace_id=pkg.trace_id,
            rule=pkg.rule,
            frame_count=len(pkg.frame_ids),
            ack=self._last_ack,
        )

    def flush_all(
        self, max_items: Optional[int] = None
    ) -> List[Union[DispatchReceipt, ErrorEvent]]:
        """Xả hàng đợi gửi về máy chủ.

        Nếu circuit breaker đang mở, chỉ thử 1 gói thăm dò (half-open); nếu gói đó vẫn lỗi
        thì dừng ngay để không làm nghẽn luồng nền. Nếu thành công, mạch đóng lại và gửi hết.
        """
        results: List[Union[DispatchReceipt, ErrorEvent]] = []
        limit = self.pending_count if max_items is None else max(0, int(max_items))
        count = 0
        while count < limit and self.pending_count > 0:
            was_open = self._circuit_open
            res = self.flush_one()
            if res is None:
                break
            results.append(res)
            count += 1
            if isinstance(res, ErrorEvent) and (was_open or self._circuit_open):
                break
        return results

    def self_test(self) -> Tuple[bool, str]:
        probe = ServerDispatcher(
            transport=lambda pkg, timeout: (True, f"ACK:{pkg.trace_id}"),
            max_outbox=4,
            max_spool=4,
            timeout_ms=200,
        )
        ev = ViolationEvent(
            trace_id="disp-selftest",
            ts_ms=_now_ms(),
            source="process.rules",
            rule="eye_drowsiness",
            t0_ms=1000,
            pre_ms=2000,
            frame_ids=(1, 2, 3),
        )
        enq_err = probe.enqueue(ev, clip_frame_ids=(1, 2, 3, 4))
        if enq_err is not None:
            return False, f"{enq_err.code}: {enq_err.message}"
        res = probe.flush_one()
        if not isinstance(res, DispatchReceipt):
            return False, "flush_one không trả về DispatchReceipt"
        return True, f"ServerDispatcher OK (frame_count={res.frame_count}, ack={res.ack})"
