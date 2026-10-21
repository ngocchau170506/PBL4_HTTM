"""Bộ thu thập và khóa khung hình chuẩn 5-10s cho từng tài xế (FEAT-021).

- Tuân thủ Luật R1: Chỉ quản lý vòng đời học tư thế chuẩn (NONE -> COLLECTING -> LOCKED -> EXPIRED).
- Tuân thủ Luật R2 & R3: Chỉ giao tiếp qua kiểu SignalPacket, BaselineProfile, ErrorEvent của contracts.
- Tuân thủ Luật R4: Nhận khoảng thời gian seconds_min, seconds_max từ cấu hình.
- Khi đã LOCKED: giữ cố định hồ sơ của tài xế đó, không học tiếp để tránh học nhầm lúc tài xế mỏi mệt.
- Khi đổi driver_id: tự động chuyển ngữ cảnh sang tài xế mới mà không làm lẫn dữ liệu.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple, Union

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId, SignalPacket
from contracts.state import BaselineProfile
from fd_process.baseline.baseline_storage import BaselineStorage


def _now_ms() -> int:
    return int(time.time() * 1000)


class BaselineCollector:
    """Thực thi NodeContract cho node process.baseline."""

    node_id: NodeId = "process.baseline"

    def __init__(
        self,
        seconds_min: int = 5,
        seconds_max: int = 10,
        min_frames: int = 5,
        storage: Optional[BaselineStorage] = None,
    ) -> None:
        self._seconds_min: int = seconds_min
        self._seconds_max: int = seconds_max
        self._min_frames: int = max(1, int(min_frames))
        self._storage: BaselineStorage = storage if storage is not None else BaselineStorage()

        self._active_driver_id: Optional[str] = None
        self._last_ts_ms_by_driver: dict[str, int] = {}
        self._valid_config: bool = (
            not isinstance(seconds_min, bool)
            and not isinstance(seconds_max, bool)
            and isinstance(seconds_min, int)
            and isinstance(seconds_max, int)
            and 0 < seconds_min <= seconds_max
        )
        self._health: Health = "OK" if self._valid_config else "DOWN"

    @property
    def storage(self) -> BaselineStorage:
        return self._storage

    @property
    def active_driver_id(self) -> Optional[str]:
        return self._active_driver_id

    @property
    def min_duration_ms(self) -> int:
        return self._seconds_min * 1000

    @property
    def max_duration_ms(self) -> int:
        return self._seconds_max * 1000

    def health(self) -> Health:
        return self._health

    def _make_error(
        self,
        trace_id: str,
        code: ErrorCode,
        err_class: str,
        message: str,
        recoverable: bool = True,
    ) -> ErrorEvent:
        safe_trace = trace_id if trace_id else "baseline-missing-trace"
        return ErrorEvent(
            trace_id=safe_trace,
            ts_ms=_now_ms(),
            source="process.baseline",
            code=code,
            node_path="process.baseline",
            err_class="config" if err_class == "config" else ("data" if err_class == "data" else "algorithm"),
            message=message,
            recoverable=recoverable,
        )

    def current_profile(self, driver_id: Optional[str] = None) -> Optional[BaselineProfile]:
        """Lấy BaselineProfile hiện tại của tài xế đang hoạt động hoặc theo driver_id chỉ định."""
        target_id = driver_id if driver_id is not None else self._active_driver_id
        if not target_id:
            return None
        return self._storage.get(target_id)

    def switch_driver(self, new_driver_id: str) -> Optional[BaselineProfile]:
        """Chuyển ngữ cảnh sang tài xế mới. Trả về hồ sơ sẵn có của tài xế đó (nếu có)."""
        clean_id = new_driver_id.strip() if isinstance(new_driver_id, str) else ""
        self._active_driver_id = clean_id if clean_id else None
        if not clean_id:
            return None
        return self._storage.get(clean_id)

    def reset_driver(
        self,
        driver_id: str,
        trace_id: str = "baseline-reset",
    ) -> Union[BaselineProfile, ErrorEvent]:
        """Hủy hạn (EXPIRED) hồ sơ của một tài xế để thu thập lại từ đầu."""
        self._last_ts_ms_by_driver.pop(driver_id.strip(), None)
        return self._storage.expire_driver(driver_id, trace_id=trace_id)

    def process_frame(
        self,
        packet: SignalPacket,
        driver_id_override: Optional[str] = None,
    ) -> Union[BaselineProfile, ErrorEvent]:
        """Nhận một SignalPacket (đã qua sampler) để cập nhật hoặc trả về BaselineProfile."""
        if not self._valid_config:
            self._health = "DOWN"
            trace_fallback = getattr(packet, "trace_id", "") or "baseline-bad-cfg"
            return self._make_error(
                trace_fallback,
                "E_CFG_INVALID",
                "config",
                f"Cấu hình baseline không hợp lệ: seconds_min={self._seconds_min}, seconds_max={self._seconds_max}",
                recoverable=False,
            )

        if not isinstance(packet, SignalPacket):
            self._health = "DEGRADED"
            return self._make_error(
                "baseline-bad-type",
                "E_SIG_BAD_FORMAT",
                "data",
                f"Đầu vào của BaselineCollector phải là SignalPacket, nhận {type(packet).__name__}",
                recoverable=True,
            )

        if not packet.trace_id or not packet.trace_id.strip():
            self._health = "DEGRADED"
            return self._make_error(
                "baseline-empty-trace",
                "E_SIG_BAD_FORMAT",
                "data",
                "SignalPacket đi vào process.baseline thiếu trace_id",
                recoverable=True,
            )

        raw_drv = driver_id_override if driver_id_override is not None else packet.driver_id
        drv = raw_drv.strip() if isinstance(raw_drv, str) else ""
        if not drv:
            self._health = "DEGRADED"
            return self._make_error(
                packet.trace_id,
                "E_PROC_BAD_OUTPUT",
                "data",
                "SignalPacket thiếu driver_id để định danh hồ sơ Baseline",
                recoverable=True,
            )

        self._active_driver_id = drv
        existing = self._storage.get(drv)

        if existing is not None and existing.state == "LOCKED":
            self._health = "OK"
            return existing

        last_ts = self._last_ts_ms_by_driver.get(drv)
        if last_ts is not None and packet.ts_ms < last_ts:
            self._health = "DEGRADED"
            return self._make_error(
                packet.trace_id,
                "E_SIG_TS_DRIFT",
                "data",
                f"Timestamp lùi khi thu thập baseline cho '{drv}': {packet.ts_ms}ms < {last_ts}ms",
                recoverable=True,
            )

        self._last_ts_ms_by_driver[drv] = packet.ts_ms

        if existing is None or existing.state in ("NONE", "EXPIRED"):
            first_profile = BaselineProfile(
                driver_id=drv,
                state="COLLECTING",
                started_ms=packet.ts_ms,
                duration_ms=0,
                frame_count=1,
            )
            saved = self._storage.save(first_profile, trace_id=packet.trace_id)
            if isinstance(saved, ErrorEvent):
                self._health = "DEGRADED"
                return saved
            self._health = "OK"
            return saved

        duration_ms = packet.ts_ms - existing.started_ms
        new_frame_count = existing.frame_count + 1

        if duration_ms > self.max_duration_ms and new_frame_count < self._min_frames:
            self._health = "DEGRADED"
            return self._make_error(
                packet.trace_id,
                "E_PROC_TIMEOUT",
                "algorithm",
                (
                    f"Thu thập baseline cho '{drv}' quá hạn {self.max_duration_ms}ms "
                    f"(thực tế {duration_ms}ms) nhưng chỉ có {new_frame_count}/{self._min_frames} frame"
                ),
                recoverable=True,
            )

        if duration_ms >= self.min_duration_ms and new_frame_count >= self._min_frames:
            clamped_duration = min(duration_ms, self.max_duration_ms)
            locked_profile = BaselineProfile(
                driver_id=drv,
                state="LOCKED",
                started_ms=existing.started_ms,
                duration_ms=clamped_duration,
                frame_count=new_frame_count,
            )
            saved = self._storage.save(locked_profile, trace_id=packet.trace_id)
            if isinstance(saved, ErrorEvent):
                self._health = "DEGRADED"
                return saved
            self._health = "OK"
            return saved

        collecting_profile = BaselineProfile(
            driver_id=drv,
            state="COLLECTING",
            started_ms=existing.started_ms,
            duration_ms=duration_ms,
            frame_count=new_frame_count,
        )
        saved = self._storage.save(collecting_profile, trace_id=packet.trace_id)
        if isinstance(saved, ErrorEvent):
            self._health = "DEGRADED"
            return saved
        self._health = "OK"
        return saved

    def self_test(self) -> Tuple[bool, str]:
        if not self._valid_config:
            return False, f"E_CFG_INVALID: min={self._seconds_min}, max={self._seconds_max}"

        probe = BaselineCollector(seconds_min=5, seconds_max=10, min_frames=5)
        for i in range(6):
            pkt = SignalPacket(
                trace_id=f"base-self-{i + 1}",
                ts_ms=1000 + i * 1000,
                source="process.sampler",
                frame_id=i + 1,
                width=640,
                height=480,
                driver_id="drv-selftest",
            )
            res = probe.process_frame(pkt)
            if isinstance(res, ErrorEvent):
                return False, f"{res.code}: {res.message}"

        prof = probe.current_profile("drv-selftest")
        if prof is None or prof.state != "LOCKED":
            return False, f"Baseline không khóa đúng sau 5s: {prof}"
        return True, f"BaselineCollector OK (LOCKED sau {prof.duration_ms}ms, {prof.frame_count} frames)"
