"""Lưu trữ, truy xuất và cách ly BaselineProfile theo từng tài xế (FEAT-022).

- Tuân thủ Luật R1: Tách biệt quản lý lưu trữ hồ sơ baseline khỏi thuật toán thu thập.
- Tuân thủ Luật R2 & R3: Chỉ dùng kiểu BaselineProfile, BaselineState, ErrorEvent từ contracts.
- Đảm bảo cách ly tuyệt đối theo driver_id: không ghi đè hồ sơ đã khóa (LOCKED) của tài xế.
"""
from __future__ import annotations

import json
import time
from typing import Dict, Optional, Tuple, Union

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId
from contracts.state import BaselineProfile, BaselineState

VALID_STATES = ("NONE", "COLLECTING", "LOCKED", "EXPIRED")


def _now_ms() -> int:
    return int(time.time() * 1000)


class BaselineStorage:
    """Kho lưu trữ cách ly BaselineProfile theo driver_id kèm xuất/nhập JSON."""

    node_id: NodeId = "process.baseline"

    def __init__(self) -> None:
        self._profiles: Dict[str, BaselineProfile] = {}
        self._health: Health = "OK"

    @property
    def count(self) -> int:
        return len(self._profiles)

    def driver_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._profiles.keys()))

    def health(self) -> Health:
        return self._health

    def _make_error(
        self,
        trace_id: str,
        code: ErrorCode,
        message: str,
        recoverable: bool = True,
    ) -> ErrorEvent:
        safe_trace = trace_id if trace_id else "baseline-store-no-trace"
        return ErrorEvent(
            trace_id=safe_trace,
            ts_ms=_now_ms(),
            source="process.baseline",
            code=code,
            node_path="process.baseline",
            err_class="data",
            message=message,
            recoverable=recoverable,
        )

    def get(self, driver_id: str) -> Optional[BaselineProfile]:
        """Lấy BaselineProfile của một tài xế, trả về None nếu chưa có."""
        if not isinstance(driver_id, str) or not driver_id.strip():
            return None
        return self._profiles.get(driver_id.strip())

    def has_locked(self, driver_id: str) -> bool:
        """Kiểm tra tài xế đã có hồ sơ chuẩn ở trạng thái LOCKED hay chưa."""
        prof = self.get(driver_id)
        return prof is not None and prof.state == "LOCKED"

    def save(
        self,
        profile: BaselineProfile,
        trace_id: str = "baseline-save",
    ) -> Union[BaselineProfile, ErrorEvent]:
        """Lưu hồ sơ của một tài xế. Chặn việc ghi đè trái phép lên hồ sơ đã LOCKED."""
        if not trace_id or not trace_id.strip():
            self._health = "DEGRADED"
            return self._make_error(
                "baseline-empty-trace",
                "E_PROC_BAD_OUTPUT",
                "trace_id không được rỗng khi lưu BaselineProfile",
            )

        if not isinstance(profile, BaselineProfile):
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_PROC_BAD_OUTPUT",
                f"Chỉ chấp nhận kiểu BaselineProfile, nhận {type(profile).__name__}",
            )

        drv = profile.driver_id.strip() if isinstance(profile.driver_id, str) else ""
        if not drv:
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_PROC_BAD_OUTPUT",
                "BaselineProfile thiếu driver_id hợp lệ",
            )

        if profile.state not in VALID_STATES:
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_PROC_BAD_OUTPUT",
                f"Trạng thái baseline không hợp lệ: {profile.state}",
            )

        if profile.duration_ms < 0 or profile.frame_count < 0:
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_PROC_BAD_OUTPUT",
                f"Thông số baseline âm không hợp lệ (duration={profile.duration_ms}, frames={profile.frame_count})",
            )

        existing = self._profiles.get(drv)
        if existing is not None and existing.state == "LOCKED":
            if profile.state == "COLLECTING":
                self._health = "DEGRADED"
                return self._make_error(
                    trace_id,
                    "E_PROC_BAD_OUTPUT",
                    f"Hồ sơ của tài xế '{drv}' đã LOCKED, không được ghi đè bằng trạng thái COLLECTING",
                )
            if (
                profile.state == "LOCKED"
                and (
                    profile.started_ms != existing.started_ms
                    or profile.duration_ms != existing.duration_ms
                    or profile.frame_count != existing.frame_count
                )
            ):
                self._health = "DEGRADED"
                return self._make_error(
                    trace_id,
                    "E_PROC_BAD_OUTPUT",
                    f"Hồ sơ của tài xế '{drv}' đã LOCKED, phải hủy hạn (EXPIRED) trước khi tạo mới",
                )

        normalized = BaselineProfile(
            driver_id=drv,
            state=profile.state,
            started_ms=profile.started_ms,
            duration_ms=profile.duration_ms,
            frame_count=profile.frame_count,
        )
        self._profiles[drv] = normalized
        self._health = "OK"
        return normalized

    def expire_driver(
        self,
        driver_id: str,
        trace_id: str = "baseline-expire",
    ) -> Union[BaselineProfile, ErrorEvent]:
        """Chuyển trạng thái hồ sơ của tài xế sang EXPIRED để cho phép học lại từ đầu."""
        existing = self.get(driver_id)
        if existing is None:
            return self._make_error(
                trace_id,
                "E_PROC_BAD_OUTPUT",
                f"Không tìm thấy hồ sơ của tài xế '{driver_id}' để chuyển sang EXPIRED",
            )
        expired = BaselineProfile(
            driver_id=existing.driver_id,
            state="EXPIRED",
            started_ms=existing.started_ms,
            duration_ms=existing.duration_ms,
            frame_count=existing.frame_count,
        )
        self._profiles[existing.driver_id] = expired
        return expired

    def remove_driver(self, driver_id: str) -> bool:
        """Xóa hoàn toàn hồ sơ của một tài xế khỏi bộ nhớ."""
        if not isinstance(driver_id, str):
            return False
        return self._profiles.pop(driver_id.strip(), None) is not None

    def export_json(self) -> str:
        """Xuất toàn bộ hồ sơ baseline ra chuỗi JSON chuẩn để lưu trữ ngoài fd_process."""
        payload = {
            drv: {
                "driver_id": p.driver_id,
                "state": p.state,
                "started_ms": p.started_ms,
                "duration_ms": p.duration_ms,
                "frame_count": p.frame_count,
            }
            for drv, p in sorted(self._profiles.items())
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def import_json(
        self,
        raw_json: str,
        trace_id: str = "baseline-import",
    ) -> Optional[ErrorEvent]:
        """Đọc và nạp lại danh sách BaselineProfile từ chuỗi JSON."""
        if not trace_id or not trace_id.strip():
            self._health = "DEGRADED"
            return self._make_error(
                "baseline-import-no-trace",
                "E_PROC_BAD_OUTPUT",
                "trace_id không được rỗng khi nạp JSON baseline",
            )
        try:
            data = json.loads(raw_json)
        except ValueError as exc:
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_PROC_BAD_OUTPUT",
                f"Chuỗi JSON baseline không hợp lệ: {exc}",
            )

        if not isinstance(data, dict):
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_PROC_BAD_OUTPUT",
                "Dữ liệu gốc của JSON baseline phải là một object (dict)",
            )

        loaded: Dict[str, BaselineProfile] = {}
        for key, item in data.items():
            if not isinstance(item, dict):
                self._health = "DEGRADED"
                return self._make_error(
                    trace_id,
                    "E_PROC_BAD_OUTPUT",
                    f"Mục hồ sơ '{key}' không phải là object",
                )
            drv = item.get("driver_id")
            state = item.get("state")
            started_ms = item.get("started_ms")
            duration_ms = item.get("duration_ms")
            frame_count = item.get("frame_count")

            if not isinstance(drv, str) or not drv.strip() or drv.strip() != key.strip():
                self._health = "DEGRADED"
                return self._make_error(
                    trace_id,
                    "E_PROC_BAD_OUTPUT",
                    f"Khóa '{key}' không khớp với driver_id '{drv}'",
                )
            if state not in VALID_STATES:
                self._health = "DEGRADED"
                return self._make_error(
                    trace_id,
                    "E_PROC_BAD_OUTPUT",
                    f"Trạng thái '{state}' trong JSON không hợp lệ",
                )
            if (
                isinstance(started_ms, bool)
                or not isinstance(started_ms, int)
                or isinstance(duration_ms, bool)
                or not isinstance(duration_ms, int)
                or duration_ms < 0
                or isinstance(frame_count, bool)
                or not isinstance(frame_count, int)
                or frame_count < 0
            ):
                self._health = "DEGRADED"
                return self._make_error(
                    trace_id,
                    "E_PROC_BAD_OUTPUT",
                    f"Số liệu thời gian/khung hình của '{drv}' sai kiểu hoặc âm",
                )

            loaded[drv.strip()] = BaselineProfile(
                driver_id=drv.strip(),
                state=state,
                started_ms=started_ms,
                duration_ms=duration_ms,
                frame_count=frame_count,
            )

        self._profiles.update(loaded)
        self._health = "OK"
        return None
