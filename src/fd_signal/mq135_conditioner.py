"""Tiền xử lý và chuẩn hóa dữ liệu cảm biến khí MQ135 (FEAT-081).

- Lọc trung bình trượt có tỉa biên (Trimmed Moving Average) 5-10 mẫu chống sụt áp nguồn xe hơi.
- Tích hợp chu kỳ sấy nóng đầu đo (Warm-up) 20s khi khởi động hệ thống.
- Chuyển đổi tín hiệu ADC 12-bit / 10-bit sang điện trở Rs và nồng độ khí PPM.
- Phân loại chất lượng không khí: AIR_NORMAL (0.0), AIR_WARNING (1.0), AIR_HAZARDOUS (2.0).
- Tuân thủ nguyên tắc không import chéo giữa các fd_* và chỉ dùng thư viện chuẩn Python (Luật R1, R2).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time
from typing import Dict, Optional, Tuple

AIR_QUALITY_NORMAL = 0.0
AIR_QUALITY_WARNING = 1.0
AIR_QUALITY_HAZARDOUS = 2.0

LABEL_WARMING_UP = "WARMING_UP"
LABEL_NORMAL = "AIR_NORMAL"
LABEL_WARNING = "AIR_WARNING"
LABEL_HAZARDOUS = "AIR_HAZARDOUS"

DEFAULT_WARMUP_PERIOD_S = 20.0
DEFAULT_WINDOW_SIZE = 5
DEFAULT_WARNING_PPM = 1000.0
DEFAULT_HAZARDOUS_PPM = 2000.0

DEFAULT_ADC_MAX = 4095.0
DEFAULT_V_REF = 3.3
DEFAULT_R_LOAD_KOHM = 10.0
DEFAULT_R0_CLEAN_AIR_KOHM = 10.0

# Tham số đường đặc tuyến MQ-135 (CO2 / Toxic Air curve: ppm = A * (Rs/R0)^(-B))
CURVE_A = 116.6020682
CURVE_B = 2.769034857


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class MQ135Reading:
    """Bản ghi dữ liệu cảm biến khí MQ135 đã chuẩn hóa."""

    mq135_ready: float
    mq135_ppm: float
    mq135_raw: float
    air_quality_level: float
    air_quality_warn: float
    warmup_remaining_s: float
    status_label: str

    def to_sensor_dict(self) -> Dict[str, float]:
        """Xuất sang từ điển số thực tương thích trường sensor của SignalPacket."""
        return {
            "mq135_ready": float(self.mq135_ready),
            "mq135_ppm": round(float(self.mq135_ppm), 2),
            "mq135_raw": round(float(self.mq135_raw), 2),
            "air_quality_level": float(self.air_quality_level),
            "air_quality_warn": float(self.air_quality_warn),
        }


def adc_to_voltage(raw_adc: float, adc_max: float = DEFAULT_ADC_MAX, v_ref: float = DEFAULT_V_REF) -> float:
    """Quy đổi giá trị ADC thô sang điện áp thực tế (V)."""
    clamped = max(0.0, min(float(raw_adc), float(adc_max)))
    return (clamped / adc_max) * float(v_ref)


def voltage_to_rs(v_out: float, v_in: float = DEFAULT_V_REF, r_load: float = DEFAULT_R_LOAD_KOHM) -> float:
    """Tính toán điện trở bề mặt cảm biến Rs (kOhm) từ điện áp ngõ ra của mạch cầu phân áp."""
    safe_vout = max(0.001, min(float(v_out), float(v_in) - 0.001))
    return float(r_load) * ((float(v_in) - safe_vout) / safe_vout)


def rs_to_ppm(
    rs: float,
    r0: float = DEFAULT_R0_CLEAN_AIR_KOHM,
    a: float = CURVE_A,
    b: float = CURVE_B,
) -> float:
    """Quy đổi tỷ lệ Rs/R0 sang nồng độ khí PPM theo đường cong chuẩn đặc tính cảm biến."""
    safe_r0 = max(0.001, float(r0))
    ratio = max(0.01, float(rs) / safe_r0)
    ppm = float(a) * (ratio ** (-float(b)))
    return max(10.0, min(10000.0, ppm))


def classify_air_quality(
    ppm: float,
    warning_ppm: float = DEFAULT_WARNING_PPM,
    hazardous_ppm: float = DEFAULT_HAZARDOUS_PPM,
) -> Tuple[float, float, str]:
    """Phân cấp an toàn không khí: level (0, 1, 2), warn flag (0.0/1.0) và nhãn chuỗi."""
    val = float(ppm)
    if val > hazardous_ppm:
        return AIR_QUALITY_HAZARDOUS, 1.0, LABEL_HAZARDOUS
    if val > warning_ppm:
        return AIR_QUALITY_WARNING, 1.0, LABEL_WARNING
    return AIR_QUALITY_NORMAL, 0.0, LABEL_NORMAL


class MQ135Conditioner:
    """Module tiền xử lý tín hiệu khí MQ135 chống sụt áp và kiểm soát thời gian warm-up."""

    def __init__(
        self,
        warmup_duration_s: float = DEFAULT_WARMUP_PERIOD_S,
        window_size: int = DEFAULT_WINDOW_SIZE,
        warning_ppm: float = DEFAULT_WARNING_PPM,
        hazardous_ppm: float = DEFAULT_HAZARDOUS_PPM,
        r_load_kohm: float = DEFAULT_R_LOAD_KOHM,
        r0_clean_air_kohm: float = DEFAULT_R0_CLEAN_AIR_KOHM,
        adc_max: float = DEFAULT_ADC_MAX,
        v_ref: float = DEFAULT_V_REF,
    ) -> None:
        if window_size < 1:
            raise ValueError("window_size phải là số nguyên dương >= 1")
        if warmup_duration_s < 0:
            raise ValueError("warmup_duration_s không được là số âm")

        self._warmup_duration_s = float(warmup_duration_s)
        self._window_size = int(window_size)
        self._warning_ppm = float(warning_ppm)
        self._hazardous_ppm = float(hazardous_ppm)
        self._r_load_kohm = float(r_load_kohm)
        self._r0_clean_air_kohm = float(r0_clean_air_kohm)
        self._adc_max = float(adc_max)
        self._v_ref = float(v_ref)

        self._raw_window: deque[float] = deque(maxlen=self._window_size)
        self._boot_ts_ms: Optional[int] = None
        self._last_reading: Optional[MQ135Reading] = None

    @property
    def warmup_duration_s(self) -> float:
        return self._warmup_duration_s

    @property
    def window_size(self) -> int:
        return self._window_size

    @property
    def latest_reading(self) -> Optional[MQ135Reading]:
        return self._last_reading

    def reset(self) -> None:
        """Khởi tạo lại trạng thái bộ đệm cửa sổ và đồng hồ warm-up."""
        self._raw_window.clear()
        self._boot_ts_ms = None
        self._last_reading = None

    def _filter_samples(self) -> float:
        """Lọc trung bình trượt tỉa biên: loại bỏ giá trị cực tiểu và cực đại khi cửa sổ đủ lớn."""
        if not self._raw_window:
            return 0.0
        items = list(self._raw_window)
        if len(items) >= 4:
            sorted_items = sorted(items)
            trimmed = sorted_items[1:-1]
            return sum(trimmed) / len(trimmed)
        return sum(items) / len(items)

    def process_sample(
        self,
        raw_adc: Optional[float] = None,
        direct_ppm: Optional[float] = None,
        ts_ms: Optional[int] = None,
    ) -> MQ135Reading:
        """Tiền xử lý một mẫu dữ liệu cảm biến: lọc nhiễu sụt áp, kiểm tra warm-up và tính PPM."""
        current_ts = ts_ms if ts_ms is not None else _now_ms()
        if self._boot_ts_ms is None:
            self._boot_ts_ms = current_ts

        elapsed_s = max(0.0, (current_ts - self._boot_ts_ms) / 1000.0)
        is_warmed_up = elapsed_s >= self._warmup_duration_s
        remaining_s = max(0.0, self._warmup_duration_s - elapsed_s)

        # Trường hợp 1: Nhận trực tiếp nồng độ PPM từ firmware (hoặc mock/replay)
        if direct_ppm is not None:
            self._raw_window.append(float(direct_ppm))
            filtered_val = self._filter_samples()
            final_ppm = filtered_val
            final_raw = filtered_val
        # Trường hợp 2: Nhận giá trị ADC thô từ mạch phân áp
        elif raw_adc is not None:
            self._raw_window.append(float(raw_adc))
            filtered_raw = self._filter_samples()
            v_out = adc_to_voltage(filtered_raw, adc_max=self._adc_max, v_ref=self._v_ref)
            rs = voltage_to_rs(v_out, v_in=self._v_ref, r_load=self._r_load_kohm)
            final_ppm = rs_to_ppm(rs, r0=self._r0_clean_air_kohm)
            final_raw = filtered_raw
        # Trường hợp 3: Mẫu rỗng hoặc không có dữ liệu
        else:
            final_ppm = 400.0
            final_raw = 0.0

        if not is_warmed_up:
            reading = MQ135Reading(
                mq135_ready=0.0,
                mq135_ppm=round(final_ppm, 2),
                mq135_raw=round(final_raw, 2),
                air_quality_level=AIR_QUALITY_NORMAL,
                air_quality_warn=0.0,
                warmup_remaining_s=round(remaining_s, 2),
                status_label=LABEL_WARMING_UP,
            )
        else:
            level, warn_flag, label = classify_air_quality(
                final_ppm,
                warning_ppm=self._warning_ppm,
                hazardous_ppm=self._hazardous_ppm,
            )
            reading = MQ135Reading(
                mq135_ready=1.0,
                mq135_ppm=round(final_ppm, 2),
                mq135_raw=round(final_raw, 2),
                air_quality_level=level,
                air_quality_warn=warn_flag,
                warmup_remaining_s=0.0,
                status_label=label,
            )

        self._last_reading = reading
        return reading

    def condition_sensor_dict(
        self,
        sensor_dict: Dict[str, float],
        ts_ms: Optional[int] = None,
    ) -> Dict[str, float]:
        """Tiền xử lý và bổ sung các trường MQ135 đã chuẩn hóa vào từ điển cảm biến."""
        direct_ppm: Optional[float] = None
        raw_adc: Optional[float] = None

        if "co2_ppm" in sensor_dict:
            direct_ppm = sensor_dict["co2_ppm"]
        elif "mq135_ppm" in sensor_dict:
            direct_ppm = sensor_dict["mq135_ppm"]
        elif "adc_raw" in sensor_dict:
            raw_adc = sensor_dict["adc_raw"]
        elif "mq135_raw" in sensor_dict:
            raw_adc = sensor_dict["mq135_raw"]

        reading = self.process_sample(raw_adc=raw_adc, direct_ppm=direct_ppm, ts_ms=ts_ms)
        enriched = dict(sensor_dict)
        enriched.update(reading.to_sensor_dict())
        return enriched
