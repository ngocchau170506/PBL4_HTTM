"""Bộ nạp và kiểm tra tính hợp lệ của cấu hình duy nhất (Luật R4).

- Chỉ đọc từ file JSON chuẩn trong fd_config, tuyệt đối không tạo biến toàn cục chia sẻ.
- Phát ErrorEvent với mã E_CFG_INVALID khi cấu hình thiếu khóa hoặc sai kiểu/giá trị.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple, Union

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
ERROR_CODE: ErrorCode = "E_CFG_INVALID"


@dataclass(frozen=True)
class CameraConfig:
    source: str
    index: int
    uri: Optional[str]
    fps: int
    width: int
    height: int


@dataclass(frozen=True)
class Esp32Config:
    enabled: bool
    uri: Optional[str]


@dataclass(frozen=True)
class SamplerConfig:
    target_fps: int


@dataclass(frozen=True)
class BaselineConfig:
    seconds_min: int
    seconds_max: int


@dataclass(frozen=True)
class AiConfig:
    adapter: str
    timeout_ms: int


@dataclass(frozen=True)
class ResponseConfig:
    pre_event_ms: int
    buffer_frames: int


@dataclass(frozen=True)
class SystemConfig:
    mode: Literal["sim", "rasp"]
    camera: CameraConfig
    esp32: Esp32Config
    sampler: SamplerConfig
    baseline: BaselineConfig
    ai: AiConfig
    response: ResponseConfig


def _now_ms() -> int:
    return int(time.time() * 1000)


def _cfg_error(trace_id: str, message: str) -> ErrorEvent:
    safe_trace = trace_id if trace_id else "cfg-missing-trace"
    return ErrorEvent(
        trace_id=safe_trace,
        ts_ms=_now_ms(),
        source="config",
        code="E_CFG_INVALID",
        node_path="config",
        err_class="config",
        message=message,
        recoverable=False,
    )


def _require_positive_int(section: str, key: str, value: Any) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return f"{section}.{key} phải là số nguyên dương"
    return None


class ConfigLoader:
    """Thực thi NodeContract cho node config."""

    node_id: NodeId = "config"

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._path: Path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._health: Health = "OK"
        self._cached: Optional[SystemConfig] = None

    @property
    def current(self) -> Optional[SystemConfig]:
        return self._cached

    def health(self) -> Health:
        return self._health

    def load(self, trace_id: str = "cfg-boot") -> Union[SystemConfig, ErrorEvent]:
        if not trace_id:
            self._health = "DOWN"
            return _cfg_error("cfg-no-trace", "trace_id không được rỗng")

        if not self._path.exists():
            self._health = "DOWN"
            return _cfg_error(trace_id, f"Không tìm thấy file cấu hình: {self._path.name}")

        try:
            raw_text = self._path.read_text(encoding="utf-8")
            raw_data = json.loads(raw_text)
        except (OSError, ValueError) as exc:
            self._health = "DOWN"
            return _cfg_error(trace_id, f"Lỗi đọc/phân tích JSON cấu hình: {exc}")

        return self.validate_dict(raw_data, trace_id=trace_id)

    def validate_dict(
        self, raw: Any, trace_id: str = "cfg-check"
    ) -> Union[SystemConfig, ErrorEvent]:
        if not trace_id:
            self._health = "DOWN"
            return _cfg_error("cfg-no-trace", "trace_id không được rỗng")

        if not isinstance(raw, dict):
            self._health = "DOWN"
            return _cfg_error(trace_id, "Cấu hình gốc phải là đối tượng JSON (dict)")

        mode = raw.get("mode")
        if mode not in ("sim", "rasp"):
            self._health = "DOWN"
            return _cfg_error(trace_id, "mode bắt buộc phải là 'sim' hoặc 'rasp'")

        cam_raw = raw.get("camera")
        if not isinstance(cam_raw, dict):
            self._health = "DOWN"
            return _cfg_error(trace_id, "Thiếu hoặc sai kiểu mục 'camera'")

        for k in ("fps", "width", "height"):
            err = _require_positive_int("camera", k, cam_raw.get(k))
            if err:
                self._health = "DOWN"
                return _cfg_error(trace_id, err)

        cam_source = cam_raw.get("source")
        if not isinstance(cam_source, str) or not cam_source.strip():
            self._health = "DOWN"
            return _cfg_error(trace_id, "camera.source phải là chuỗi không rỗng")

        cam_index = cam_raw.get("index", 0)
        if isinstance(cam_index, bool) or not isinstance(cam_index, int) or cam_index < 0:
            self._health = "DOWN"
            return _cfg_error(trace_id, "camera.index phải là số nguyên không âm")

        cam_uri = cam_raw.get("uri")
        if cam_uri is not None and not isinstance(cam_uri, str):
            self._health = "DOWN"
            return _cfg_error(trace_id, "camera.uri phải là chuỗi hoặc null")
        if cam_source in ("rtsp", "video", "file") and (not cam_uri or not cam_uri.strip()):
            self._health = "DOWN"
            return _cfg_error(trace_id, f"camera.uri bắt buộc phải có khi camera.source={cam_source}")

        esp_raw = raw.get("esp32")
        if not isinstance(esp_raw, dict):
            self._health = "DOWN"
            return _cfg_error(trace_id, "Thiếu hoặc sai kiểu mục 'esp32'")

        esp_enabled = esp_raw.get("enabled")
        if not isinstance(esp_enabled, bool):
            self._health = "DOWN"
            return _cfg_error(trace_id, "esp32.enabled phải là kiểu bool")

        esp_uri = esp_raw.get("uri")
        if esp_uri is not None and not isinstance(esp_uri, str):
            self._health = "DOWN"
            return _cfg_error(trace_id, "esp32.uri phải là chuỗi hoặc null")
        if esp_enabled and (not esp_uri or not esp_uri.strip()):
            self._health = "DOWN"
            return _cfg_error(trace_id, "esp32.uri bắt buộc phải có khi esp32.enabled=true")

        sampler_raw = raw.get("sampler")
        if not isinstance(sampler_raw, dict):
            self._health = "DOWN"
            return _cfg_error(trace_id, "Thiếu hoặc sai kiểu mục 'sampler'")

        err = _require_positive_int("sampler", "target_fps", sampler_raw.get("target_fps"))
        if err:
            self._health = "DOWN"
            return _cfg_error(trace_id, err)
        if sampler_raw["target_fps"] > cam_raw["fps"]:
            self._health = "DOWN"
            return _cfg_error(
                trace_id,
                f"sampler.target_fps ({sampler_raw['target_fps']}) không được lớn hơn camera.fps ({cam_raw['fps']})",
            )

        base_raw = raw.get("baseline")
        if not isinstance(base_raw, dict):
            self._health = "DOWN"
            return _cfg_error(trace_id, "Thiếu hoặc sai kiểu mục 'baseline'")

        for k in ("seconds_min", "seconds_max"):
            err = _require_positive_int("baseline", k, base_raw.get(k))
            if err:
                self._health = "DOWN"
                return _cfg_error(trace_id, err)

        if base_raw["seconds_min"] > base_raw["seconds_max"]:
            self._health = "DOWN"
            return _cfg_error(trace_id, "baseline.seconds_min không được lớn hơn seconds_max")

        ai_raw = raw.get("ai")
        if not isinstance(ai_raw, dict):
            self._health = "DOWN"
            return _cfg_error(trace_id, "Thiếu hoặc sai kiểu mục 'ai'")

        ai_adapter = ai_raw.get("adapter")
        if ai_adapter not in ("stub", "yolo"):
            self._health = "DOWN"
            return _cfg_error(trace_id, "ai.adapter phải là 'stub' hoặc 'yolo'")

        err = _require_positive_int("ai", "timeout_ms", ai_raw.get("timeout_ms"))
        if err:
            self._health = "DOWN"
            return _cfg_error(trace_id, err)

        resp_raw = raw.get("response")
        if not isinstance(resp_raw, dict):
            self._health = "DOWN"
            return _cfg_error(trace_id, "Thiếu hoặc sai kiểu mục 'response'")

        for k in ("pre_event_ms", "buffer_frames"):
            err = _require_positive_int("response", k, resp_raw.get(k))
            if err:
                self._health = "DOWN"
                return _cfg_error(trace_id, err)

        min_buffer = (resp_raw["pre_event_ms"] * sampler_raw["target_fps"] + 999) // 1000
        if resp_raw["buffer_frames"] < min_buffer:
            self._health = "DOWN"
            return _cfg_error(
                trace_id,
                f"response.buffer_frames ({resp_raw['buffer_frames']}) nhỏ hơn mức tối thiểu {min_buffer} cho pre_event_ms",
            )

        cfg = SystemConfig(
            mode=mode,
            camera=CameraConfig(
                source=cam_source,
                index=cam_index,
                uri=cam_uri,
                fps=cam_raw["fps"],
                width=cam_raw["width"],
                height=cam_raw["height"],
            ),
            esp32=Esp32Config(
                enabled=esp_enabled,
                uri=esp_uri,
            ),
            sampler=SamplerConfig(target_fps=sampler_raw["target_fps"]),
            baseline=BaselineConfig(
                seconds_min=base_raw["seconds_min"],
                seconds_max=base_raw["seconds_max"],
            ),
            ai=AiConfig(
                adapter=ai_adapter,
                timeout_ms=ai_raw["timeout_ms"],
            ),
            response=ResponseConfig(
                pre_event_ms=resp_raw["pre_event_ms"],
                buffer_frames=resp_raw["buffer_frames"],
            ),
        )
        self._cached = cfg
        self._health = "OK"
        return cfg

    def self_test(self) -> Tuple[bool, str]:
        res = self.load(trace_id="cfg-selftest")
        if isinstance(res, ErrorEvent):
            return False, f"{res.code}: {res.message}"
        return True, f"Nạp cấu hình thành công (mode={res.mode}, cam={res.camera.width}x{res.camera.height}@{res.camera.fps}fps)"
