"""Bộ nạp và kiểm tra tính hợp lệ của cấu hình duy nhất (Luật R4).

- Chỉ đọc từ file JSON chuẩn trong fd_config, tuyệt đối không tạo biến toàn cục chia sẻ.
- Phát ErrorEvent với mã E_CFG_INVALID khi cấu hình thiếu khóa hoặc sai kiểu/giá trị.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Mapping, Optional, Tuple, Union

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
DEFAULT_DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"
ERROR_CODE: ErrorCode = "E_CFG_INVALID"
_PLAINTEXT_PWD_RE = re.compile(r"password=(?!\$\{[A-Za-z_][A-Za-z0-9_]*\})[^&_?\s]+", re.I)


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
class EyeDrowsinessRuleConfig:
    trigger_ms: int
    closed_labels: Tuple[str, ...]
    open_labels: Tuple[str, ...]


@dataclass(frozen=True)
class MouthYawnRuleConfig:
    trigger_ms: int
    yawn_labels: Tuple[str, ...]
    no_yawn_labels: Tuple[str, ...]


@dataclass(frozen=True)
class RulesConfig:
    min_conf: float
    max_gap_ms: int
    eye_drowsiness: EyeDrowsinessRuleConfig
    mouth_yawn: MouthYawnRuleConfig
    status: Optional[str] = None


@dataclass(frozen=True)
class EvalConfig:
    min_throughput_fps: float
    max_p95_latency_ms: float
    max_p99_latency_ms: float
    max_queue_depth: int
    max_ram_growth_mb: float
    sustained_test_duration_s: int


@dataclass(frozen=True)
class SystemConfig:
    mode: Literal["sim", "rasp"]
    camera: CameraConfig
    esp32: Esp32Config
    sampler: SamplerConfig
    baseline: BaselineConfig
    ai: AiConfig
    response: ResponseConfig
    rules: Optional[RulesConfig] = None
    eval: Optional[EvalConfig] = None


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


def parse_dotenv_file(dotenv_path: Path) -> Dict[str, str]:
    """Đọc các cặp KEY=VALUE từ file .env (nếu tồn tại); chỉ dùng thư viện chuẩn."""
    result: Dict[str, str] = {}
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return result
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        k = key.strip()
        v = val.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k:
            result[k] = v
    return result


def _require_label_list(
    section: str, key: str, value: Any
) -> Tuple[Optional[Tuple[str, ...]], Optional[str]]:
    if not isinstance(value, list) or not value:
        return None, f"{section}.{key} phải là danh sách chuỗi không rỗng"
    cleaned = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None, f"{section}.{key} chỉ được chứa chuỗi không rỗng"
        cleaned.append(item.strip())
    return tuple(cleaned), None


def _parse_rules_section(
    rules_raw: Any,
) -> Tuple[Optional[RulesConfig], Optional[str]]:
    if not isinstance(rules_raw, dict):
        return None, "Thiếu hoặc sai kiểu mục 'rules'"

    min_conf = rules_raw.get("min_conf")
    if (
        isinstance(min_conf, bool)
        or not isinstance(min_conf, (int, float))
        or not (0.0 < float(min_conf) <= 1.0)
    ):
        return None, "rules.min_conf phải là số trong nửa khoảng (0, 1]"

    err = _require_positive_int("rules", "max_gap_ms", rules_raw.get("max_gap_ms"))
    if err:
        return None, err

    eye_raw = rules_raw.get("eye_drowsiness")
    if not isinstance(eye_raw, dict):
        return None, "Thiếu hoặc sai kiểu mục 'rules.eye_drowsiness'"

    err = _require_positive_int("rules.eye_drowsiness", "trigger_ms", eye_raw.get("trigger_ms"))
    if err:
        return None, err

    closed_labels, err = _require_label_list(
        "rules.eye_drowsiness", "closed_labels", eye_raw.get("closed_labels")
    )
    if err or closed_labels is None:
        return None, err

    open_labels, err = _require_label_list(
        "rules.eye_drowsiness", "open_labels", eye_raw.get("open_labels")
    )
    if err or open_labels is None:
        return None, err

    if set(closed_labels) & set(open_labels):
        return (
            None,
            "rules.eye_drowsiness.closed_labels và open_labels không được trùng nhau",
        )

    yawn_raw = rules_raw.get("mouth_yawn")
    if not isinstance(yawn_raw, dict):
        return None, "Thiếu hoặc sai kiểu mục 'rules.mouth_yawn'"

    err = _require_positive_int("rules.mouth_yawn", "trigger_ms", yawn_raw.get("trigger_ms"))
    if err:
        return None, err

    yawn_labels, err = _require_label_list(
        "rules.mouth_yawn", "yawn_labels", yawn_raw.get("yawn_labels")
    )
    if err or yawn_labels is None:
        return None, err

    no_yawn_labels, err = _require_label_list(
        "rules.mouth_yawn", "no_yawn_labels", yawn_raw.get("no_yawn_labels")
    )
    if err or no_yawn_labels is None:
        return None, err

    if set(yawn_labels) & set(no_yawn_labels):
        return (
            None,
            "rules.mouth_yawn.yawn_labels và no_yawn_labels không được trùng nhau",
        )

    status_val = rules_raw.get("_status")
    if status_val is not None and not isinstance(status_val, str):
        return None, "rules._status phải là chuỗi hoặc null"

    return (
        RulesConfig(
            min_conf=float(min_conf),
            max_gap_ms=rules_raw["max_gap_ms"],
            eye_drowsiness=EyeDrowsinessRuleConfig(
                trigger_ms=eye_raw["trigger_ms"],
                closed_labels=closed_labels,
                open_labels=open_labels,
            ),
            mouth_yawn=MouthYawnRuleConfig(
                trigger_ms=yawn_raw["trigger_ms"],
                yawn_labels=yawn_labels,
                no_yawn_labels=no_yawn_labels,
            ),
            status=status_val,
        ),
        None,
    )


def _require_positive_number(section: str, key: str, value: Any) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0.0:
        return f"{section}.{key} phải là số dương"
    return None


def _parse_eval_section(
    eval_raw: Any,
) -> Tuple[Optional[EvalConfig], Optional[str]]:
    if not isinstance(eval_raw, dict):
        return None, "Thiếu hoặc sai kiểu mục 'eval'"

    for num_key in (
        "min_throughput_fps",
        "max_p95_latency_ms",
        "max_p99_latency_ms",
        "max_ram_growth_mb",
    ):
        err = _require_positive_number("eval", num_key, eval_raw.get(num_key))
        if err:
            return None, err

    for int_key in ("max_queue_depth", "sustained_test_duration_s"):
        err = _require_positive_int("eval", int_key, eval_raw.get(int_key))
        if err:
            return None, err

    p95 = float(eval_raw["max_p95_latency_ms"])
    p99 = float(eval_raw["max_p99_latency_ms"])
    if p99 < p95:
        return None, "eval.max_p99_latency_ms không được nhỏ hơn eval.max_p95_latency_ms"

    return (
        EvalConfig(
            min_throughput_fps=float(eval_raw["min_throughput_fps"]),
            max_p95_latency_ms=p95,
            max_p99_latency_ms=p99,
            max_queue_depth=eval_raw["max_queue_depth"],
            max_ram_growth_mb=float(eval_raw["max_ram_growth_mb"]),
            sustained_test_duration_s=eval_raw["sustained_test_duration_s"],
        ),
        None,
    )


class ConfigLoader:
    """Thực thi NodeContract cho node config."""

    node_id: NodeId = "config"

    def __init__(
        self,
        config_path: Optional[Path] = None,
        dotenv_path: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._path: Path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._dotenv_path: Path = (
            Path(dotenv_path) if dotenv_path else DEFAULT_DOTENV_PATH
        )
        self._env_override: Optional[Mapping[str, str]] = env
        self._health: Health = "OK"
        self._cached: Optional[SystemConfig] = None

    def _effective_env(self) -> Dict[str, str]:
        merged = parse_dotenv_file(self._dotenv_path)
        if self._env_override is not None:
            for k, v in self._env_override.items():
                if isinstance(k, str) and isinstance(v, str):
                    merged[k] = v
        else:
            for k, v in os.environ.items():
                merged[k] = v
        return merged

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
        if isinstance(cam_uri, str) and _PLAINTEXT_PWD_RE.search(cam_uri):
            self._health = "DOWN"
            return _cfg_error(
                trace_id,
                "camera.uri không được chứa mật khẩu văn bản thường; hãy dùng ${FD_CAMERA_PASSWORD} hoặc FD_CAMERA_URI",
            )
        env_map = self._effective_env()
        env_uri = env_map.get("FD_CAMERA_URI", "").strip()
        if env_uri:
            cam_uri = env_uri
        elif isinstance(cam_uri, str) and "${FD_CAMERA_PASSWORD}" in cam_uri:
            env_pwd = env_map.get("FD_CAMERA_PASSWORD", "").strip()
            if env_pwd:
                cam_uri = cam_uri.replace("${FD_CAMERA_PASSWORD}", env_pwd)
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

        rules_cfg, rules_err = _parse_rules_section(raw.get("rules"))
        if rules_err or rules_cfg is None:
            self._health = "DOWN"
            return _cfg_error(trace_id, rules_err or "Lỗi mục 'rules'")

        eval_cfg: Optional[EvalConfig] = None
        if "eval" in raw:
            eval_cfg, eval_err = _parse_eval_section(raw.get("eval"))
            if eval_err or eval_cfg is None:
                self._health = "DOWN"
                return _cfg_error(trace_id, eval_err or "Lỗi mục 'eval'")

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
            rules=rules_cfg,
            eval=eval_cfg,
        )
        self._cached = cfg
        self._health = "OK"
        return cfg

    def self_test(self) -> Tuple[bool, str]:
        res = self.load(trace_id="cfg-selftest")
        if isinstance(res, ErrorEvent):
            return False, f"{res.code}: {res.message}"
        return True, f"Nạp cấu hình thành công (mode={res.mode}, cam={res.camera.width}x{res.camera.height}@{res.camera.fps}fps)"
