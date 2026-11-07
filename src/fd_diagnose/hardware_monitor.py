"""Bộ giám sát phần cứng nhúng Raspberry Pi 4 (src/fd_diagnose/hardware_monitor.py).

- Thu thập thông số phần cứng thực tế: nhiệt độ SoC (thermal_zone0/vcgencmd) và cờ bóp xung (throttled).
- Phát hiện cảnh báo quá nhiệt (> 80°C), sụt áp nguồn (under-voltage), bóp xung CPU để ghi nhận vào ErrorRecorder.
- Tuyệt đối chỉ sử dụng thư viện chuẩn (Luật R4/R7).
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId

_TEMP_RE = re.compile(r"temp=([\d\.]+)'C")
_SYSFS_THERMAL_PATH = Path("/sys/class/thermal/thermal_zone0/temp")


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class HardwareSnapshot:
    """Ảnh chụp tức thời các chỉ số phần cứng SoC."""

    ts_ms: int
    temp_c: Optional[float]
    raw_throttled: int
    under_voltage_now: bool
    freq_capped_now: bool
    throttled_now: bool
    soft_temp_limit_now: bool
    under_voltage_occurred: bool
    freq_capped_occurred: bool
    throttled_occurred: bool
    soft_temp_limit_occurred: bool

    @property
    def is_healthy(self) -> bool:
        """Hệ thống không gặp sụt áp hoặc bóp xung tức thời và nhiệt độ an toàn."""
        if self.temp_c is not None and self.temp_c >= 80.0:
            return False
        if self.under_voltage_now or self.throttled_now:
            return False
        return True


def parse_throttled_bitmask(raw_val: int) -> Dict[str, bool]:
    """Giải mã 20-bit cờ trạng thái nguồn và xung nhịp của Raspberry Pi."""
    val = int(raw_val)
    return {
        "under_voltage_now": bool(val & (1 << 0)),
        "freq_capped_now": bool(val & (1 << 1)),
        "throttled_now": bool(val & (1 << 2)),
        "soft_temp_limit_now": bool(val & (1 << 3)),
        "under_voltage_occurred": bool(val & (1 << 16)),
        "freq_capped_occurred": bool(val & (1 << 17)),
        "throttled_occurred": bool(val & (1 << 18)),
        "soft_temp_limit_occurred": bool(val & (1 << 19)),
    }


def parse_vcgencmd_throttled_output(output_str: str) -> int:
    """Trích xuất giá trị nguyên từ đầu ra lệnh 'vcgencmd get_throttled' (ví dụ: throttled=0x50005)."""
    clean_str = output_str.strip()
    if "=" in clean_str:
        clean_str = clean_str.split("=", 1)[1].strip()
    try:
        return int(clean_str, 16)
    except (ValueError, TypeError):
        return 0


def parse_vcgencmd_temp_output(output_str: str) -> Optional[float]:
    """Trích xuất nhiệt độ từ chuỗi đầu ra của vcgencmd measure_temp."""
    match = _TEMP_RE.search(output_str)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


class HardwareMonitor:
    """Giám sát tình trạng phần cứng SoC Raspberry Pi 4."""

    node_id: NodeId = "diagnose"

    def __init__(
        self,
        temp_limit_c: float = 80.0,
        thermal_path: Optional[Path] = None,
    ) -> None:
        self._temp_limit_c = float(temp_limit_c)
        self._thermal_path = thermal_path or _SYSFS_THERMAL_PATH
        self._health: Health = "OK"
        self._last_snapshot: Optional[HardwareSnapshot] = None
        self._last_error: Optional[str] = None

    @property
    def health_state(self) -> Health:
        return self._health

    def health(self) -> Health:
        return self._health

    @property
    def last_snapshot(self) -> Optional[HardwareSnapshot]:
        return self._last_snapshot

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def read_temperature(self) -> Optional[float]:
        """Đọc nhiệt độ CPU từ sysfs hoặc lệnh vcgencmd."""
        if self._thermal_path.exists():
            try:
                raw_text = self._thermal_path.read_text(encoding="utf-8").strip()
                return float(raw_text) / 1000.0
            except (OSError, ValueError) as exc:
                self._last_error = f"Lỗi đọc sysfs thermal: {exc}"

        try:
            res = subprocess.run(
                ["vcgencmd", "measure_temp"],
                capture_output=True,
                text=True,
                timeout=0.3,
            )
            if res.returncode == 0:
                return parse_vcgencmd_temp_output(res.stdout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._last_error = f"vcgencmd measure_temp không khả dụng: {exc}"

        return None

    def read_throttled(self) -> int:
        """Đọc thanh ghi trạng thái throttling qua lệnh vcgencmd."""
        try:
            res = subprocess.run(
                ["vcgencmd", "get_throttled"],
                capture_output=True,
                text=True,
                timeout=0.3,
            )
            if res.returncode == 0:
                return parse_vcgencmd_throttled_output(res.stdout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._last_error = f"vcgencmd get_throttled không khả dụng: {exc}"

        return 0

    def capture_snapshot(self) -> HardwareSnapshot:
        """Thu thập toàn bộ thông số phần cứng hiện tại."""
        temp_c = self.read_temperature()
        raw_throttled = self.read_throttled()
        flags = parse_throttled_bitmask(raw_throttled)

        snap = HardwareSnapshot(
            ts_ms=_now_ms(),
            temp_c=temp_c,
            raw_throttled=raw_throttled,
            under_voltage_now=flags["under_voltage_now"],
            freq_capped_now=flags["freq_capped_now"],
            throttled_now=flags["throttled_now"],
            soft_temp_limit_now=flags["soft_temp_limit_now"],
            under_voltage_occurred=flags["under_voltage_occurred"],
            freq_capped_occurred=flags["freq_capped_occurred"],
            throttled_occurred=flags["throttled_occurred"],
            soft_temp_limit_occurred=flags["soft_temp_limit_occurred"],
        )
        self._last_snapshot = snap
        return snap

    def check_hardware(
        self, trace_id: str = "hw-check"
    ) -> Tuple[HardwareSnapshot, Optional[ErrorEvent]]:
        """Kiểm tra tình trạng phần cứng; phát sinh ErrorEvent nếu có sự cố nghiêm trọng."""
        snap = self.capture_snapshot()
        safe_trace = trace_id if trace_id else "hw-no-trace"

        error_reasons = []
        if snap.temp_c is not None and snap.temp_c >= self._temp_limit_c:
            error_reasons.append(
                f"Quá nhiệt CPU ({snap.temp_c:.1f}°C >= {self._temp_limit_c:.1f}°C)"
            )
        if snap.under_voltage_now:
            error_reasons.append("Sụt áp nguồn cấp (Under-voltage detected)")
        if snap.throttled_now:
            error_reasons.append("CPU đang bị bóp xung nghiêm trọng (Throttled active)")

        if error_reasons:
            self._health = "DEGRADED"
            detail = "; ".join(error_reasons)
            err = ErrorEvent(
                trace_id=safe_trace,
                ts_ms=snap.ts_ms,
                source="diagnose",
                code="E_FLOW_NODE_DOWN",
                node_path="diagnose.hardware",
                err_class="performance",
                message=f"Sự cố phần cứng nhúng: {detail}",
                recoverable=True,
            )
            return snap, err

        self._health = "OK"
        return snap, None

    def self_test(self) -> Tuple[bool, str]:
        """Tự kiểm tra chức năng thu thập dữ liệu phần cứng theo NodeContract."""
        snap, err = self.check_hardware(trace_id="hw-selftest")
        if err is not None:
            return False, f"{err.code}: {err.message}"
        temp_str = f"{snap.temp_c:.1f}°C" if snap.temp_c is not None else "N/A"
        return True, f"HardwareMonitor OK (temp={temp_str}, throttled=0x{snap.raw_throttled:x})"
