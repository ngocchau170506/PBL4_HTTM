"""Bộ thu thập số liệu đánh giá hạ tầng luồng dữ liệu (FEAT-050).

- Đo thông lượng thực tế (FPS), phân vị độ trễ từng chặng (P50, P90, P95, P99, Max),
  chiều sâu hàng đợi (queue_depth) và mức tăng trưởng bộ nhớ (RAM).
- Không can thiệp hay thiết kế thuật toán AI; chỉ quan sát hạ tầng luồng.
- Sử dụng thuật toán Welford trực tuyến và lấy mẫu hồ chứa (reservoir sampling)
  cố định kích thước để bảo đảm không rò rỉ bộ nhớ khi chạy dài hạn.
- Chỉ dùng thư viện chuẩn của Python và kiểu dữ liệu trong contracts.logic.
"""
from __future__ import annotations

import gc
import math
import random
import time
import tracemalloc
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from contracts.logic import ErrorEvent, Health, NodeId, PacketBase


def _now_ms() -> int:
    return int(time.time() * 1000)


def _current_heap_mb() -> float:
    if not tracemalloc.is_tracing():
        tracemalloc.start()
    current_bytes, _ = tracemalloc.get_traced_memory()
    return current_bytes / (1024.0 * 1024.0)


@dataclass(frozen=True)
class EvalThresholds:
    """Ngưỡng đánh giá hạ tầng G5 (nạp từ cấu hình fd_config)."""

    min_throughput_fps: float = 3000.0
    max_p95_latency_ms: float = 1.0
    max_p99_latency_ms: float = 5.0
    max_queue_depth: int = 100
    max_ram_growth_mb: float = 5.0


@dataclass(frozen=True)
class StageMetrics:
    """Số liệu thống kê của từng chặng trong cây luồng."""

    stage: str
    count: int
    fps: float
    mean_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float
    p50_latency_ms: float
    p90_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float


@dataclass(frozen=True)
class MetricsSummary:
    """Bản tổng hợp số liệu toàn phiên đánh giá hạ tầng."""

    total_packets: int
    elapsed_ms: float
    overall_fps: float
    mean_latency_ms: float
    p50_latency_ms: float
    p90_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    peak_queue_depth: int
    ram_baseline_mb: float
    ram_peak_mb: float
    ram_growth_mb: float
    error_count: int
    error_by_code: Dict[str, int]
    stages: Dict[str, StageMetrics]
    passed_thresholds: bool
    violations: Tuple[str, ...]


class _StreamingStat:
    """Thống kê trực tuyến kết hợp Welford và Reservoir Sampling cố định bộ nhớ."""

    def __init__(self, reservoir_size: int = 1024, seed: int = 42) -> None:
        if reservoir_size <= 0:
            raise ValueError("reservoir_size phải lớn hơn 0")
        self._capacity: int = reservoir_size
        self._rng: random.Random = random.Random(seed)
        self._count: int = 0
        self._mean: float = 0.0
        self._m2: float = 0.0
        self._min_val: float = math.inf
        self._max_val: float = 0.0
        self._reservoir: List[float] = []

    @property
    def count(self) -> int:
        return self._count

    @property
    def mean(self) -> float:
        return self._mean if self._count > 0 else 0.0

    @property
    def min_value(self) -> float:
        return self._min_val if self._count > 0 else 0.0

    @property
    def max_value(self) -> float:
        return self._max_val if self._count > 0 else 0.0

    @property
    def sample_size(self) -> int:
        return len(self._reservoir)

    def add(self, value: float) -> None:
        val = float(value)
        self._count += 1
        delta = val - self._mean
        self._mean += delta / self._count
        delta2 = val - self._mean
        self._m2 += delta * delta2
        if val < self._min_val:
            self._min_val = val
        if val > self._max_val:
            self._max_val = val

        if len(self._reservoir) < self._capacity:
            self._reservoir.append(val)
        else:
            idx = self._rng.randint(0, self._count - 1)
            if idx < self._capacity:
                self._reservoir[idx] = val

    def percentiles(self) -> Tuple[float, float, float, float]:
        if not self._reservoir:
            return 0.0, 0.0, 0.0, 0.0
        sorted_samples = sorted(self._reservoir)
        n = len(sorted_samples)

        def _pick(q: float) -> float:
            if n == 1:
                return sorted_samples[0]
            pos = q * (n - 1)
            lo = int(math.floor(pos))
            hi = int(math.ceil(pos))
            if lo == hi:
                return sorted_samples[lo]
            weight = pos - lo
            return sorted_samples[lo] * (1.0 - weight) + sorted_samples[hi] * weight

        return _pick(0.50), _pick(0.90), _pick(0.95), _pick(0.99)

    def clear(self) -> None:
        self._count = 0
        self._mean = 0.0
        self._m2 = 0.0
        self._min_val = math.inf
        self._max_val = 0.0
        self._reservoir.clear()


class MetricsCollector:
    """Thực thi NodeContract cho node eval (FEAT-050)."""

    node_id: NodeId = "eval"

    def __init__(
        self,
        thresholds: Optional[EvalThresholds] = None,
        reservoir_size: int = 1024,
        seed: int = 42,
    ) -> None:
        if reservoir_size <= 0:
            raise ValueError("reservoir_size phải là số nguyên dương")
        self._thresholds: EvalThresholds = thresholds or EvalThresholds()
        self._reservoir_size: int = reservoir_size
        self._seed: int = seed
        self._health: Health = "OK"
        self._overall_stat: _StreamingStat = _StreamingStat(reservoir_size, seed)
        self._stage_stats: Dict[str, _StreamingStat] = {}
        self._error_by_code: Dict[str, int] = {}
        self._error_count: int = 0
        self._peak_queue_depth: int = 0
        self._first_ts_ms: Optional[int] = None
        self._last_ts_ms: Optional[int] = None
        self._start_perf_s: float = time.perf_counter()
        self._ram_baseline_mb: float = _current_heap_mb()
        self._ram_peak_mb: float = self._ram_baseline_mb

    @property
    def thresholds(self) -> EvalThresholds:
        return self._thresholds

    def health(self) -> Health:
        return self._health

    def record_hop(
        self,
        stage: str,
        latency_ms: float,
        ts_ms: Optional[int] = None,
        queue_depth: int = 0,
        trace_id: str = "eval-hop",
    ) -> Optional[ErrorEvent]:
        """Ghi nhận một bước chuyển tiếp gói tin trên chặng luồng."""
        now_val = ts_ms if ts_ms is not None else _now_ms()
        safe_trace = trace_id.strip() if isinstance(trace_id, str) and trace_id.strip() else "eval-missing-trace"

        if not isinstance(stage, str) or not stage.strip():
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id=safe_trace,
                ts_ms=now_val,
                source="eval",
                code="E_PROC_BAD_OUTPUT",
                node_path="eval",
                err_class="data",
                message="Tên chặng (stage) không được rỗng",
                recoverable=True,
            )

        if isinstance(latency_ms, bool) or not isinstance(latency_ms, (int, float)) or math.isnan(latency_ms) or latency_ms < 0.0:
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id=safe_trace,
                ts_ms=now_val,
                source="eval",
                code="E_PROC_BAD_OUTPUT",
                node_path=f"eval.{stage.strip()}",
                err_class="data",
                message=f"latency_ms không hợp lệ ({latency_ms})",
                recoverable=True,
            )

        if isinstance(queue_depth, bool) or not isinstance(queue_depth, int) or queue_depth < 0:
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id=safe_trace,
                ts_ms=now_val,
                source="eval",
                code="E_PROC_BAD_OUTPUT",
                node_path=f"eval.{stage.strip()}",
                err_class="data",
                recoverable=True,
                message=f"queue_depth không hợp lệ ({queue_depth})",
            )

        clean_stage = stage.strip()
        if self._first_ts_ms is None or now_val < self._first_ts_ms:
            self._first_ts_ms = now_val
        if self._last_ts_ms is None or now_val > self._last_ts_ms:
            self._last_ts_ms = now_val

        self._overall_stat.add(float(latency_ms))
        stage_stat = self._stage_stats.get(clean_stage)
        if stage_stat is None:
            stage_stat = _StreamingStat(self._reservoir_size, self._seed)
            self._stage_stats[clean_stage] = stage_stat
        stage_stat.add(float(latency_ms))

        if queue_depth > self._peak_queue_depth:
            self._peak_queue_depth = queue_depth

        if self._overall_stat.count % 512 == 0:
            self.sample_memory()

        if queue_depth > self._thresholds.max_queue_depth:
            self._health = "DEGRADED"
            err = ErrorEvent(
                trace_id=safe_trace,
                ts_ms=now_val,
                source="eval",
                code="E_FLOW_QUEUE_FULL",
                node_path=f"eval.{clean_stage}",
                err_class="performance",
                message=(
                    f"Chiều sâu hàng đợi tại {clean_stage} ({queue_depth}) "
                    f"vượt ngưỡng cho phép ({self._thresholds.max_queue_depth})"
                ),
                recoverable=True,
            )
            self.record_error(err)
            return err

        return None

    def record_packet(
        self,
        packet: PacketBase,
        latency_ms: float = 0.0,
        queue_depth: int = 0,
    ) -> Optional[ErrorEvent]:
        """Quan sát trực tiếp một gói tin chuẩn từ cây luồng."""
        if not isinstance(packet, PacketBase):
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id="eval-bad-packet",
                ts_ms=_now_ms(),
                source="eval",
                code="E_FLOW_EDGE_TYPE",
                node_path="eval",
                err_class="contract",
                message="Đối tượng đưa vào record_packet không phải PacketBase",
                recoverable=True,
            )
        if not packet.trace_id or not packet.trace_id.strip():
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id="eval-empty-trace",
                ts_ms=packet.ts_ms,
                source="eval",
                code="E_SIG_BAD_FORMAT",
                node_path=f"eval.{packet.source}",
                err_class="contract",
                message="Gói tin thiếu trace_id hợp lệ",
                recoverable=True,
            )
        return self.record_hop(
            stage=str(packet.source),
            latency_ms=latency_ms,
            ts_ms=packet.ts_ms,
            queue_depth=queue_depth,
            trace_id=packet.trace_id,
        )

    def record_error(self, event: ErrorEvent) -> None:
        """Ghi nhận thống kê mã lỗi phát sinh mà không tích trữ đối tượng gây tốn RAM."""
        if not isinstance(event, ErrorEvent):
            return
        self._error_count += 1
        code_key = str(event.code)
        self._error_by_code[code_key] = self._error_by_code.get(code_key, 0) + 1

    def sample_memory(self) -> float:
        """Lấy mẫu bộ nhớ heap hiện tại và cập nhật mức đỉnh."""
        current_mb = _current_heap_mb()
        if current_mb > self._ram_peak_mb:
            self._ram_peak_mb = current_mb
        return current_mb

    def release_memory(self) -> float:
        """Chủ động thu gom rác giải phóng bộ nhớ và trả về mức RAM hiện tại (MB)."""
        gc.collect()
        return self.sample_memory()

    def summarize(self, elapsed_override_ms: Optional[float] = None) -> MetricsSummary:
        """Tổng hợp toàn bộ số liệu và đối chiếu với ngưỡng cấu hình."""
        self.sample_memory()

        if elapsed_override_ms is not None and elapsed_override_ms > 0.0:
            elapsed_ms = float(elapsed_override_ms)
        else:
            perf_elapsed_ms = (time.perf_counter() - self._start_perf_s) * 1000.0
            ts_span_ms = 0.0
            if self._first_ts_ms is not None and self._last_ts_ms is not None:
                ts_span_ms = float(max(0, self._last_ts_ms - self._first_ts_ms))
            elapsed_ms = max(perf_elapsed_ms, ts_span_ms, 0.001)

        total_count = self._overall_stat.count
        overall_fps = (total_count * 1000.0 / elapsed_ms) if total_count > 0 else 0.0
        p50, p90, p95, p99 = self._overall_stat.percentiles()

        stage_summaries: Dict[str, StageMetrics] = {}
        for st_name, st_stat in self._stage_stats.items():
            st_p50, st_p90, st_p95, st_p99 = st_stat.percentiles()
            st_fps = (st_stat.count * 1000.0 / elapsed_ms) if st_stat.count > 0 else 0.0
            stage_summaries[st_name] = StageMetrics(
                stage=st_name,
                count=st_stat.count,
                fps=st_fps,
                mean_latency_ms=st_stat.mean,
                min_latency_ms=st_stat.min_value,
                max_latency_ms=st_stat.max_value,
                p50_latency_ms=st_p50,
                p90_latency_ms=st_p90,
                p95_latency_ms=st_p95,
                p99_latency_ms=st_p99,
            )

        ram_growth_mb = max(0.0, self._ram_peak_mb - self._ram_baseline_mb)
        violations: List[str] = []

        if total_count > 0 and overall_fps < self._thresholds.min_throughput_fps:
            violations.append(
                f"overall_fps ({overall_fps:.2f}) < min_throughput_fps ({self._thresholds.min_throughput_fps:.2f})"
            )
        if p95 > self._thresholds.max_p95_latency_ms:
            violations.append(
                f"p95_latency_ms ({p95:.4f}) > max_p95_latency_ms ({self._thresholds.max_p95_latency_ms:.4f})"
            )
        if p99 > self._thresholds.max_p99_latency_ms:
            violations.append(
                f"p99_latency_ms ({p99:.4f}) > max_p99_latency_ms ({self._thresholds.max_p99_latency_ms:.4f})"
            )
        if self._peak_queue_depth > self._thresholds.max_queue_depth:
            violations.append(
                f"peak_queue_depth ({self._peak_queue_depth}) > max_queue_depth ({self._thresholds.max_queue_depth})"
            )
        if ram_growth_mb > self._thresholds.max_ram_growth_mb:
            violations.append(
                f"ram_growth_mb ({ram_growth_mb:.3f}) > max_ram_growth_mb ({self._thresholds.max_ram_growth_mb:.3f})"
            )

        return MetricsSummary(
            total_packets=total_count,
            elapsed_ms=elapsed_ms,
            overall_fps=overall_fps,
            mean_latency_ms=self._overall_stat.mean,
            p50_latency_ms=p50,
            p90_latency_ms=p90,
            p95_latency_ms=p95,
            p99_latency_ms=p99,
            max_latency_ms=self._overall_stat.max_value,
            peak_queue_depth=self._peak_queue_depth,
            ram_baseline_mb=self._ram_baseline_mb,
            ram_peak_mb=self._ram_peak_mb,
            ram_growth_mb=ram_growth_mb,
            error_count=self._error_count,
            error_by_code=dict(self._error_by_code),
            stages=stage_summaries,
            passed_thresholds=(len(violations) == 0),
            violations=tuple(violations),
        )

    def reset(self) -> None:
        """Đặt lại toàn bộ bộ đếm và giải phóng bộ nhớ đệm."""
        self._overall_stat.clear()
        for stat in self._stage_stats.values():
            stat.clear()
        self._stage_stats.clear()
        self._error_by_code.clear()
        self._error_count = 0
        self._peak_queue_depth = 0
        self._first_ts_ms = None
        self._last_ts_ms = None
        self._health = "OK"
        gc.collect()
        self._start_perf_s = time.perf_counter()
        self._ram_baseline_mb = _current_heap_mb()
        self._ram_peak_mb = self._ram_baseline_mb

    def self_test(self) -> Tuple[bool, str]:
        """Tự kiểm tra nhanh tính toàn vẹn của bộ thu thập số liệu."""
        probe = MetricsCollector(
            thresholds=EvalThresholds(
                min_throughput_fps=10.0,
                max_p95_latency_ms=10.0,
                max_p99_latency_ms=20.0,
                max_queue_depth=50,
                max_ram_growth_mb=10.0,
            ),
            reservoir_size=64,
        )
        for idx in range(20):
            err = probe.record_hop("signal", latency_ms=0.2 + idx * 0.01, ts_ms=1000 + idx, queue_depth=2)
            if err is not None:
                return False, f"Lỗi khi tự kiểm tra record_hop: {err.message}"
        summary = probe.summarize(elapsed_override_ms=100.0)
        probe.reset()
        if summary.total_packets != 20 or not summary.passed_thresholds:
            return False, f"Tự kiểm tra thất bại: packets={summary.total_packets}, violations={summary.violations}"
        return True, f"MetricsCollector sẵn sàng (fps={summary.overall_fps:.1f}, p95={summary.p95_latency_ms:.3f}ms)"
