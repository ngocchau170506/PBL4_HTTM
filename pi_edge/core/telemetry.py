"""Thread-safe HardwareSampler, REAL L0-L3 ThermalPolicy, and 4-Tier FPS + Alert Latency tracker."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class ThrottlePlan:
    thermal_level: int
    ai_min_interval_s: float
    yolo_imgsz: int
    allow_face_geometry: bool
    reason: str


class ThermalPolicy:
    """Real thermal governor: ACTUALLY drops imgsz to 256 at L3 (>=80°C) & spaces AI interval."""

    def __init__(self, base_imgsz: int = 320, warm_c: float = 70.0, hot_c: float = 75.0, crit_c: float = 80.0):
        self.base_imgsz = int(base_imgsz)
        self.warm_c = float(warm_c)
        self.hot_c = float(hot_c)
        self.crit_c = float(crit_c)

    def plan(self, temp_c: Optional[float], throttled_bits: int = 0) -> ThrottlePlan:
        # Check ALL 4 active throttle/undervolt/soft-temp bits (0xF); sticky history bits 0xF0000 reported separately
        undervolt_or_throttled = bool(throttled_bits & 0xF)
        t = float(temp_c) if temp_c is not None else 0.0
        if undervolt_or_throttled or t >= self.crit_c:
            return ThrottlePlan(
                thermal_level=3,
                ai_min_interval_s=0.40,
                yolo_imgsz=min(self.base_imgsz, 256),
                allow_face_geometry=False,
                reason="L3_CRITICAL_THERMAL_OR_THROTTLE",
            )
        if t >= self.hot_c:
            return ThrottlePlan(
                thermal_level=2,
                ai_min_interval_s=0.25,
                yolo_imgsz=self.base_imgsz,
                allow_face_geometry=True,
                reason="L2_HOT",
            )
        if t >= self.warm_c:
            return ThrottlePlan(
                thermal_level=1,
                ai_min_interval_s=0.15,
                yolo_imgsz=self.base_imgsz,
                allow_face_geometry=True,
                reason="L1_WARM",
            )
        return ThrottlePlan(
            thermal_level=0,
            ai_min_interval_s=0.08,
            yolo_imgsz=self.base_imgsz,
            allow_face_geometry=True,
            reason="L0_NORMAL",
        )


class HardwareSampler:
    """Thread-safe background hardware sampler (respects HARDWARE_POLL_INTERVAL_S, has clean close())."""

    def __init__(self, interval_s: float = 5.0):
        self.interval_s = max(1.0, float(interval_s))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._history: deque = deque(maxlen=24)
        self._data: Dict[str, object] = {
            "cpu_percent": 0.0,
            "ram_mb": 0.0,
            "temp_c": None,
            "core_volt_v": None,
            "throttled": "0x0",
            "throttled_bits": 0,
            "undervolt_occurred": False,
            "vcgencmd_ok": True,
        }
        self._last_vcgencmd_mono = -999.0
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hw-sampler", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _read_temp_c(self) -> Optional[float]:
        p = Path("/sys/class/thermal/thermal_zone0/temp")
        if p.exists():
            try:
                return round(float(p.read_text().strip()) / 1000.0, 1)
            except Exception:
                return None
        return None

    def _read_throttled(self) -> tuple[str, int, bool]:
        try:
            out = subprocess.check_output(["vcgencmd", "get_throttled"], timeout=0.8, text=True).strip()
            hex_part = out.split("=")[-1].strip()
            return hex_part, int(hex_part, 16), True
        except Exception:
            return "0x0", 0, False

    def _read_core_volt(self) -> Optional[float]:
        try:
            out = subprocess.check_output(["vcgencmd", "measure_volts", "core"], timeout=0.8, text=True).strip()
            val = out.split("=")[-1].strip().rstrip("Vv")
            return round(float(val), 4)
        except Exception:
            return None

    def sample_once(self) -> Dict[str, object]:
        cpu_pct = 0.0
        ram_mb = 0.0
        try:
            import psutil
            cpu_pct = round(float(psutil.cpu_percent(interval=None)), 1)
            ram_mb = round(float(psutil.Process(os.getpid()).memory_info().rss) / (1024 * 1024), 1)
        except Exception:
            pass
        temp_c = self._read_temp_c()
        now_m = time.monotonic()
        with self._lock:
            throttled_str = str(self._data.get("throttled", "0x0"))
            throttled_bits = int(self._data.get("throttled_bits", 0))
            core_volt_v = self._data.get("core_volt_v")
            vcg_ok = bool(self._data.get("vcgencmd_ok", True))
        if (now_m - self._last_vcgencmd_mono) >= 10.0:
            throttled_str, throttled_bits, vcg_ok = self._read_throttled()
            core_volt_v = self._read_core_volt()
            self._last_vcgencmd_mono = now_m

        snap = {
            "cpu_percent": cpu_pct,
            "ram_mb": ram_mb,
            "temp_c": temp_c,
            "core_volt_v": core_volt_v,
            "throttled": throttled_str,
            "throttled_bits": throttled_bits,
            "undervolt_occurred": bool(throttled_bits & 0x10001),
            "vcgencmd_ok": vcg_ok,
        }
        with self._lock:
            self._data = snap
            if temp_c is not None:
                self._history.append((now_m, temp_c))
        return dict(snap)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:
                pass
            self._stop.wait(self.interval_s)

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return dict(self._data)


class MultiTierMetrics:
    """Separates Camera FPS, Loop FPS, AI Inference FPS, Stream FPS, and Alert Latency p95 (fixes F-015)."""

    def __init__(self, window_s: float = 5.0):
        self.window_s = float(window_s)
        self._lock = threading.Lock()
        self._ticks = {
            "camera": deque(),
            "loop": deque(),
            "ai": deque(),
            "stream": deque(),
        }
        self._alert_latencies_ms: deque = deque(maxlen=100)

    def mark(self, stage: str) -> None:
        now = time.monotonic()
        with self._lock:
            dq = self._ticks.get(stage)
            if dq is None:
                return
            dq.append(now)
            cutoff = now - self.window_s
            while dq and dq[0] < cutoff:
                dq.popleft()

    def record_alert_latency_ms(self, latency_ms: float) -> None:
        with self._lock:
            self._alert_latencies_ms.append(max(0.0, float(latency_ms)))

    def snapshot(self) -> Dict[str, float]:
        now = time.monotonic()
        cutoff = now - self.window_s
        out: Dict[str, float] = {}
        with self._lock:
            for k, dq in self._ticks.items():
                while dq and dq[0] < cutoff:
                    dq.popleft()
                if len(dq) >= 2:
                    span = max(0.25, dq[-1] - dq[0])
                    out[f"{k}_fps"] = round((len(dq) - 1) / span, 2)
                else:
                    out[f"{k}_fps"] = 0.0
            lats = sorted(self._alert_latencies_ms)
            if lats:
                idx = min(len(lats) - 1, int(0.95 * (len(lats) - 1)))
                out["alert_latency_p95_ms"] = round(lats[idx], 2)
            else:
                out["alert_latency_p95_ms"] = 0.0
        return out
