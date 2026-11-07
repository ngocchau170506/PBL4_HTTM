"""Quản lý tài nguyên phần cứng nhúng, điều tiết nhiệt thích ứng & ghi telemetry (Chuẩn R1).

Vai trò của module này (IOTv4):
1. configure_embedded_threads(): khóa số luồng OpenMP/BLAS/PyTorch/OpenCV và GHIM tiến trình AI vào
   tối đa 3 nhân (nhân còn lại dành cho OS, mạng, UART) để XNNPACK/TFLite của MediaPipe không
   chiếm đủ 4 nhân gây vọt nhiệt.
2. HardwareSampler: luồng nền duy nhất đọc nhiệt SoC, CPU %, RAM, cờ throttle (vcgencmd).
   get_system_metrics() chỉ đọc bộ đệm -> KHÔNG BAO GIỜ fork() tiến trình con trên luồng suy luận
   hay luồng web (trước đây có thể chặn tới 0.8s).
3. ThermalPolicy: máy trạng thái 4 mức (L0..L3) có trễ (hysteresis), dự báo theo độ dốc nhiệt
   (dT/dt) và đọc cờ throttle thật của Pi -> hạ tải TRƯỚC khi chạm 80°C.
4. EmbeddedFpsGovernor: áp kế hoạch của ThermalPolicy -> FPS mục tiêu, trần duty-cycle CPU,
   và lịch chạy luân phiên YOLO / MediaPipe (kèm TTL giữ kết quả cũ cho mô hình bị bỏ qua).
5. Telemetry: ghi nhật ký hiệu năng định kỳ ra file CSV.
"""
from __future__ import annotations

import csv
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Dict, Optional

try:
    import psutil
    _CURRENT_PROC = psutil.Process()
    _CURRENT_PROC.cpu_percent(interval=None)
    psutil.cpu_percent(interval=None)
except Exception:
    psutil = None
    _CURRENT_PROC = None

_SYSFS_THERMAL = Path("/sys/class/thermal/thermal_zone0/temp")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return float(default)


def configure_embedded_threads(max_threads: int = 2) -> None:
    """Giới hạn luồng OpenMP / BLAS / PyTorch / OpenCV và ghim CPU affinity cho Raspberry Pi 4."""
    t_str = str(os.getenv("TORCH_THREADS", str(max_threads)))
    for env_key in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ.setdefault(env_key, t_str)

    # Ghim tiến trình vào nhân 0-2 (MediaPipe/TFLite tự tạo pool = số nhân, bỏ qua OMP_NUM_THREADS).
    # Nhân 3 luôn rảnh cho kernel, mạng (RTSP), UART ESP32 và web -> hệ thống không "đơ" khi AI bận.
    if hasattr(os, "sched_setaffinity"):
        try:
            cores = sorted(os.sched_getaffinity(0))
            spec = os.getenv("AI_CPU_CORES", "").strip()
            if spec:
                wanted = {int(x) for x in spec.split(",") if x.strip().isdigit()}
            elif len(cores) >= 4:
                wanted = set(cores[:-1])
            else:
                wanted = set()
            wanted &= set(cores)
            if wanted:
                os.sched_setaffinity(0, wanted)
        except Exception:
            pass

    try:
        import cv2
        cv2.setNumThreads(int(t_str))
    except Exception:
        pass

    try:
        import torch
        torch.set_num_threads(int(t_str))
        torch.set_num_interop_threads(1)
    except Exception:
        pass


def read_soc_temperature_c() -> float | None:
    """Đọc trực tiếp nhiệt độ chip SoC từ sysfs (không tạo tiến trình con)."""
    if _SYSFS_THERMAL.exists():
        try:
            return round(float(_SYSFS_THERMAL.read_text().strip()) / 1000.0, 1)
        except Exception:
            pass
    return None


class HardwareSampler:
    """Luồng nền duy nhất lấy mẫu phần cứng; mọi nơi khác chỉ đọc snapshot (không chặn)."""

    _instance: Optional["HardwareSampler"] = None
    _instance_lock = threading.Lock()

    def __init__(self, interval_s: float = 1.0, vcgencmd_interval_s: float = 10.0):
        self.interval_s = max(0.5, float(interval_s))
        self.vcgencmd_interval_s = max(2.0, float(vcgencmd_interval_s))
        self._lock = threading.Lock()
        self._history: deque = deque(maxlen=12)  # (monotonic, temp_c)
        self._data: Dict[str, Any] = {
            "cpu_percent": 0.0,
            "ram_mb": 0.0,
            "temp_c": None,
            "throttled": "0x0",
        }
        self._throttled_bits = 0
        self._last_vcgencmd = 0.0
        self._stop = threading.Event()
        self._sample_once()
        self._thread = threading.Thread(target=self._run, name="hw-sampler", daemon=True)
        self._thread.start()

    @classmethod
    def get(cls) -> "HardwareSampler":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _sample_once(self) -> None:
        now = time.monotonic()
        data = dict(self._data)
        if psutil is not None and _CURRENT_PROC is not None:
            try:
                data["cpu_percent"] = round(float(psutil.cpu_percent(interval=None)), 1)
                data["ram_mb"] = round(_CURRENT_PROC.memory_info().rss / (1024 * 1024), 1)
            except Exception:
                pass
        temp_c = read_soc_temperature_c()
        if temp_c is not None:
            data["temp_c"] = temp_c
            self._history.append((now, temp_c))

        if _SYSFS_THERMAL.exists() and (now - self._last_vcgencmd) >= self.vcgencmd_interval_s:
            self._last_vcgencmd = now
            try:
                res = subprocess.check_output(
                    ["vcgencmd", "get_throttled"], text=True, timeout=0.8, stderr=subprocess.DEVNULL
                ).strip()
                if "=" in res:
                    raw = res.split("=")[-1].strip()
                    self._throttled_bits = int(raw, 16)
                    data["throttled"] = raw
            except Exception:
                pass
        with self._lock:
            self._data = data

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self._sample_once()
            except Exception:
                pass

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def temp_slope_c_per_s(self) -> float:
        """Độ dốc nhiệt trung bình trong ~10s gần nhất (°C/s, dương = đang nóng lên)."""
        h = list(self._history)
        if len(h) < 3:
            return 0.0
        dt = h[-1][0] - h[0][0]
        if dt < 3.0:
            return 0.0
        return (h[-1][1] - h[0][1]) / dt

    @property
    def throttled_bits(self) -> int:
        return self._throttled_bits


def get_system_metrics() -> Dict[str, Any]:
    """CPU %, RAM MB, nhiệt SoC, cờ throttle. Đọc bộ đệm của HardwareSampler (không chặn)."""
    return HardwareSampler.get().snapshot()


@dataclass(frozen=True)
class ThrottlePlan:
    """Kế hoạch tải cho một nhịp xử lý."""

    level: int            # 0..3
    fps: float            # FPS mục tiêu của vòng AI
    duty_cap: float       # tỉ lệ tối đa thời gian CPU bận / chu kỳ (1.0 = không giới hạn)
    run_yolo: bool
    run_mp: bool
    hold_s: float         # TTL giữ kết quả cũ cho mô hình bị bỏ qua
    imgsz: int            # kích thước ảnh YOLO


class ThermalPolicy:
    """Máy trạng thái nhiệt 4 mức, có trễ + dự báo độ dốc + cờ throttle thật của Pi."""

    def __init__(self):
        self.warm_c = _env_float("THERMAL_WARM_C", 58.0)
        self.hot_c = _env_float("THERMAL_HOT_C", 65.0)
        self.crit_c = _env_float("THERMAL_CRIT_C", 72.0)
        self.hysteresis_c = _env_float("THERMAL_HYSTERESIS_C", 3.0)
        self.down_dwell_s = _env_float("THERMAL_DOWN_DWELL_S", 12.0)
        self.lookahead_s = _env_float("THERMAL_LOOKAHEAD_S", 8.0)
        self.level = 0
        self._below_since: Optional[float] = None
        self.effective_temp_c: Optional[float] = None

    def _raw_level(self, t: float) -> int:
        if t >= self.crit_c:
            return 3
        if t >= self.hot_c:
            return 2
        if t >= self.warm_c:
            return 1
        return 0

    def update(self, temp_c: Optional[float], slope_c_s: float, throttled_bits: int, now: float) -> int:
        if temp_c is None:
            eff = None
            target = 0
        else:
            # Dự báo: nếu đang nóng nhanh thì coi như đã nóng hơn (tối đa +8°C)
            eff = temp_c + max(0.0, min(8.0, slope_c_s * self.lookahead_s))
            target = self._raw_level(eff)
        # Cờ Pi báo ĐANG bị giới hạn xung (bit1 freq cap, bit2 throttled, bit3 soft temp limit)
        if throttled_bits & 0xE:
            target = max(target, 2)
        elif throttled_bits & 0x1:  # sụt áp nguồn: giảm tải để bớt dòng đỉnh
            target = max(target, 1)
        self.effective_temp_c = eff

        if target > self.level:
            self.level = target
            self._below_since = None
        elif target < self.level:
            # Chỉ hạ mức khi nhiệt thấp hơn ngưỡng - hysteresis đủ lâu
            thr = (self.warm_c, self.hot_c, self.crit_c)[self.level - 1] - self.hysteresis_c
            cool = temp_c is None or (eff is not None and eff < thr)
            if cool and not (throttled_bits & 0xF):
                if self._below_since is None:
                    self._below_since = now
                elif now - self._below_since >= self.down_dwell_s:
                    self.level -= 1
                    self._below_since = None
            else:
                self._below_since = None
        else:
            self._below_since = None
        return self.level


class EmbeddedFpsGovernor:
    """Bộ điều tiết nhịp suy luận AI: FPS + duty-cycle + lịch luân phiên YOLO/MediaPipe theo nhiệt độ."""

    def __init__(self, base_fps: float | None = None, sampler: Optional[HardwareSampler] = None):
        self.base_fps = float(base_fps if base_fps is not None else os.getenv("SAMPLER_TARGET_FPS", "5.0"))
        self.base_imgsz = int(os.getenv("YOLO_IMGSZ", "320"))
        self._sampler = sampler or HardwareSampler.get()
        self.policy = ThermalPolicy()
        self.current_target_fps = self.base_fps
        self._tick = 0
        self._last_policy_check = 0.0
        self._slot_start = 0.0
        self._plan = self._build_plan(0)

    @property
    def level(self) -> int:
        return self.policy.level

    @property
    def plan(self) -> ThrottlePlan:
        return self._plan

    def _build_plan(self, level: int) -> ThrottlePlan:
        b = self.base_fps
        if level <= 0:
            fps, duty, every_y, every_m, imgsz = b, 1.0, 1, 1, self.base_imgsz
        elif level == 1:
            fps, duty, every_y, every_m, imgsz = min(b, 4.0), 0.80, 1, 2, self.base_imgsz
        elif level == 2:
            fps, duty, every_y, every_m, imgsz = min(b, 3.0), 0.60, 2, 2, self.base_imgsz
        else:
            # L3 giữ imgsz tối thiểu 320 (nếu base_imgsz >= 320) để không mất các box mắt nhỏ (Eye_Closed/Eye_Open),
            # vì tần suất YOLO đã giảm còn 1 Hz (fps<=2, every_y=2) và duty_cap=0.40 đã đủ hạ nhiệt.
            l3_imgsz = min(self.base_imgsz, 320) if self.base_imgsz >= 320 else self.base_imgsz
            fps, duty, every_y, every_m, imgsz = min(b, 2.0), 0.40, 2, 2, l3_imgsz
        interval = 1.0 / max(0.5, fps)
        hold = max(every_y, every_m) * interval * 1.25 + 0.25
        # Lịch luân phiên: mỗi model chạy khi tick % every == offset (YOLO offset 0, MP offset 1)
        run_y = (self._tick % every_y) == 0
        run_m = (self._tick % every_m) == (1 % every_m) if every_m > 1 else True
        if every_y == 1 and every_m == 1:
            run_y = run_m = True
        if not run_y and not run_m:  # không để nhịp rỗng
            run_y = True
        return ThrottlePlan(level, fps, duty, run_y, run_m, hold, imgsz)

    def wait_for_next_slot(self) -> ThrottlePlan:
        """Nghỉ đủ để giữ FPS + duty-cycle, trả về kế hoạch cho nhịp sắp chạy."""
        now = time.monotonic()
        if (now - self._last_policy_check) >= 1.0:
            self._last_policy_check = now
            snap = self._sampler.snapshot()
            self.policy.update(
                snap.get("temp_c"),
                self._sampler.temp_slope_c_per_s(),
                self._sampler.throttled_bits,
                now,
            )

        plan = self._build_plan(self.policy.level)
        self.current_target_fps = plan.fps
        if self._slot_start > 0.0:
            work_s = now - self._slot_start
            min_interval = 1.0 / plan.fps
            sleep_s = min_interval - work_s
            if plan.duty_cap < 1.0:
                # Ví dụ duty 0.4: làm 100ms thì phải nghỉ ≥150ms
                sleep_s = max(sleep_s, work_s * (1.0 / plan.duty_cap - 1.0))
            if sleep_s > 0.005:
                time.sleep(min(sleep_s, 2.0))
        self._slot_start = time.monotonic()
        self._tick += 1
        self._plan = plan
        return plan


class Telemetry:
    fields = (
        "timestamp", "ai_fps", "yolo_latency_ms", "mp_latency_ms",
        "pipeline_latency_ms", "frame_age_ms", "alert_latency_ms",
        "rss_mb", "cpu_percent", "temp_c", "throttled", "eye_source",
        "mouth_source", "confirmed_alert", "esp_connected", "esp_acknowledged",
    )

    def __init__(self, path, interval=5.0):
        self.path = Path(path) if path else None
        self.interval = interval
        self.last = float("-inf")
        self.file = None

    def record(self, state):
        now = time.monotonic()
        if self.path is None or now - self.last < self.interval:
            return
        self.last = now
        row = {key: state.get(key) for key in self.fields}
        hw = get_system_metrics()
        row["rss_mb"] = hw.get("ram_mb")
        row["cpu_percent"] = hw.get("cpu_percent")
        row["temp_c"] = hw.get("temp_c")
        row["throttled"] = hw.get("throttled")
        result = state.get("esp_result")
        row["esp_acknowledged"] = getattr(result, "acknowledged", None)
        try:
            if self.file is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                has_header = self.path.exists() and self.path.stat().st_size > 0
                self.file = self.path.open("a", newline="", encoding="utf-8")
                self.writer = csv.DictWriter(self.file, fieldnames=self.fields)
                if not has_header:
                    self.writer.writeheader()
            self.writer.writerow(row)
            self.file.flush()
        except OSError as exc:
            print(f"[TELEMETRY] {exc}")
            self.close()

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None
