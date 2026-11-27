"""Lightweight Pi Edge configuration & single RUN_MODE=local|server resolver (zero heavy imports)."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Mapping, Optional

RunMode = Literal["local", "server"]
EnvProfile = Literal["prod", "lab"]

PI_EDGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PI_EDGE_ROOT.parent


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    val = env.get(key)
    if val is None or val == "":
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    try:
        v = env.get(key)
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    try:
        v = env.get(key)
        return int(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def resolve_run_profile(
    env: Optional[Mapping[str, str]] = None,
    cli_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Single authoritative RUN_MODE=local|server resolver (fixes F-006 / LCK-04)."""
    e = env if env is not None else os.environ
    raw_mode = (cli_mode or e.get("RUN_MODE") or "server").strip().lower()
    if raw_mode not in ("local", "server"):
        raise ValueError(f"RUN_MODE khong hop le: '{raw_mode}' (chi chap nhan 'local' hoac 'server')")
    run_mode: RunMode = raw_mode  # type: ignore[assignment]

    raw_profile = (e.get("ENV_PROFILE") or "prod").strip().lower()
    if raw_profile not in ("prod", "lab"):
        raise ValueError(f"ENV_PROFILE khong hop le: '{raw_profile}' (chi chap nhan 'prod' hoac 'lab')")
    env_profile: EnvProfile = raw_profile  # type: ignore[assignment]

    web_port = _env_int(e, "WEB_PORT", 8080)
    if web_port <= 0:
        web_port = 8080

    # Strict separation (LCK-04 v2):
    # - local  : full dashboard + /stream on Pi, ZERO server egress (remote_enabled=False unconditionally!)
    # - server : ultra-light web (/api/v1/health, /api/esp_bridge), live /stream OFF by default to save Pi CPU & 4G bandwidth
    remote_enabled = run_mode == "server"
    enable_live_stream = _env_bool(e, "ENABLE_LIVE_STREAM", run_mode == "local")
    bind_host = "127.0.0.1" if env_profile == "lab" else "0.0.0.0"

    return {
        "run_mode": run_mode,
        "env_profile": env_profile,
        "remote_enabled": remote_enabled,
        "enable_live_stream": enable_live_stream,
        "web_enabled": True,
        "web_mode": "full_local" if run_mode == "local" else "minimal_server",
        "web_host": bind_host,
        "web_port": web_port,
        "headless": True,
    }


@dataclass(frozen=True)
class EdgeConfig:
    run_mode: RunMode
    env_profile: EnvProfile
    remote_enabled: bool
    enable_live_stream: bool
    web_mode: str
    web_host: str
    web_port: int

    vehicle_id: str
    vehicle_name: str
    driver_name: str

    disable_camera: bool
    disable_ai: bool
    camera_uri: str
    camera_ip: str
    camera_width: int
    camera_height: int
    camera_fps: float
    camera_rotation: int
    camera_prefer_substream: bool
    camera_stall_reset_s: float

    yolo_model_path: str
    landmarker_path: str
    yolo_imgsz: int
    yolo_max_det: int
    torch_threads: int
    opencv_threads: int
    ai_cpu_cores: str

    eye_threshold_s: float
    yawn_threshold_s: float
    distracted_threshold_s: float
    blind_hold_max_s: float
    allow_smoking: bool
    allow_phone: bool
    smoking_conf: float
    phone_conf: float

    esp_port: str
    esp_baudrate: int
    esp_mode: str
    esp_require_ack: bool

    esp_ble_port: str
    esp_ble_baudrate: int
    esp_ble_mode: str

    server_url: str
    telemetry_interval_s: float
    burst_max_frames: int
    burst_jpeg_quality: int
    max_spool_mb: float

    hardware_poll_interval_s: float
    perf_log_interval_s: float
    thermal_warm_c: float
    thermal_hot_c: float
    thermal_crit_c: float

    storage_dir: Path
    logs_dir: Path
    audit_dir: Path
    calibration_dir: Path

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, cli_mode: Optional[str] = None) -> "EdgeConfig":
        e = env if env is not None else os.environ
        prof = resolve_run_profile(e, cli_mode=cli_mode)
        # Hard clamp blind hold <= 0.8s (LCK-06: prevents single false frame from hitting 2.0s alert threshold!)
        raw_hold = _env_float(e, "BLIND_HOLD_MAX_S", 0.75)
        blind_hold = max(0.1, min(0.8, raw_hold))

        storage_dir = Path(e.get("VIOLATION_STORAGE_DIR") or (PI_EDGE_ROOT / "violations"))
        logs_dir = Path(e.get("LOGS_DIR") or (PI_EDGE_ROOT / "logs"))
        audit_dir = Path(e.get("AUDIT_DIR") or (logs_dir / "audit"))
        calibration_dir = Path(e.get("CALIBRATION_DIR") or (PI_EDGE_ROOT / "calibration"))

        return cls(
            run_mode=prof["run_mode"],
            env_profile=prof["env_profile"],
            remote_enabled=prof["remote_enabled"],
            enable_live_stream=prof["enable_live_stream"],
            web_mode=prof["web_mode"],
            web_host=prof["web_host"],
            web_port=prof["web_port"],
            vehicle_id=(e.get("VEHICLE_ID") or "XE-01").strip().strip('"'),
            vehicle_name=(e.get("VEHICLE_NAME") or "Xe Giam Sat PI_AI").strip().strip('"'),
            driver_name=(e.get("DRIVER_NAME") or "Tai xe 01").strip().strip('"'),
            disable_camera=_env_bool(e, "DISABLE_CAMERA", False),
            disable_ai=_env_bool(e, "DISABLE_AI", False),
            camera_uri=(e.get("FD_CAMERA_URI") or "").strip().strip('"').strip("'"),
            camera_ip=(e.get("FD_CAMERA_IP") or "192.168.1.210").strip(),
            camera_width=_env_int(e, "CAMERA_WIDTH", 640),
            camera_height=_env_int(e, "CAMERA_HEIGHT", 480),
            camera_fps=_env_float(e, "CAMERA_FPS", 30.0),
            camera_rotation=_env_int(e, "CAMERA_ROTATION", 180),
            camera_prefer_substream=_env_bool(e, "CAMERA_PREFER_SUBSTREAM", True),
            camera_stall_reset_s=_env_float(e, "CAMERA_STALL_RESET_S", 6.0),
            yolo_model_path=(e.get("YOLO_MODEL_PATH") or str(PI_EDGE_ROOT / "models" / "best.pt")),
            landmarker_path=(e.get("LANDMARKER_PATH") or str(PI_EDGE_ROOT / "models" / "face_landmarker.task")),
            yolo_imgsz=_env_int(e, "YOLO_IMGSZ", 320),
            yolo_max_det=_env_int(e, "YOLO_MAX_DET", 8),
            torch_threads=max(1, _env_int(e, "TORCH_THREADS", 2)),
            opencv_threads=max(1, _env_int(e, "OPENCV_THREADS", 1)),
            ai_cpu_cores=(e.get("AI_CPU_CORES") or "0,1,2").strip(),
            eye_threshold_s=_env_float(e, "DROWSY_THRESHOLD_SECONDS", 2.0),
            yawn_threshold_s=_env_float(e, "YAWNING_THRESHOLD_SECONDS", 1.8),
            distracted_threshold_s=_env_float(e, "DISTRACTED_THRESHOLD_SECONDS", 2.0),
            blind_hold_max_s=blind_hold,
            allow_smoking=_env_bool(e, "ALLOW_SMOKING_ALERTS", True),
            allow_phone=_env_bool(e, "ALLOW_PHONE_ALERTS", True),
            smoking_conf=_env_float(e, "YOLO_SMOKING_CONF", 0.45),
            phone_conf=_env_float(e, "YOLO_PHONE_CONF", 0.45),
            esp_port=(e.get("ESP_PORT") or e.get("FD_ESP32_URI") or "/dev/ttyUSB0").strip(),
            esp_baudrate=_env_int(e, "ESP_BAUDRATE", 115200),
            esp_mode=(e.get("ESP_MODE") or "SERIAL").strip().upper(),
            esp_require_ack=_env_bool(e, "ESP_REQUIRE_ACK", True),
            esp_ble_port=(e.get("ESP_BLE_PORT") or "/dev/esp_ble").strip(),
            esp_ble_baudrate=_env_int(e, "ESP_BLE_BAUDRATE", 115200),
            esp_ble_mode=(e.get("ESP_BLE_MODE") or "DISABLED").strip().upper(),
            server_url=(e.get("REMOTE_SERVER_URL") or "http://192.168.1.133:5000").strip().rstrip("/"),
            telemetry_interval_s=max(0.5, _env_float(e, "TELEMETRY_INTERVAL_S", 1.5)),
            burst_max_frames=max(4, min(20, _env_int(e, "BURST_MAX_FRAMES", 14))),
            burst_jpeg_quality=max(40, min(90, _env_int(e, "EVIDENCE_JPEG_QUALITY", 70))),
            max_spool_mb=max(32.0, _env_float(e, "MAX_SPOOL_MB", 512.0)),
            hardware_poll_interval_s=max(1.0, _env_float(e, "HARDWARE_POLL_INTERVAL_S", 5.0)),
            perf_log_interval_s=max(5.0, _env_float(e, "PERF_LOG_INTERVAL", 30.0)),
            thermal_warm_c=_env_float(e, "THERMAL_WARM_C", 70.0),
            thermal_hot_c=_env_float(e, "THERMAL_HOT_C", 75.0),
            thermal_crit_c=_env_float(e, "THERMAL_CRIT_C", 80.0),
            storage_dir=storage_dir,
            logs_dir=logs_dir,
            audit_dir=audit_dir,
            calibration_dir=calibration_dir,
        )


def _file_sha256_short(path: str) -> Optional[str]:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def write_runtime_manifest(cfg: EdgeConfig) -> Path:
    """Emit redacted Effective Runtime Manifest (fixes F-016; zero plaintext secrets!)."""
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)
    # Redact any credential-bearing camera URI
    if data.get("camera_uri"):
        data["camera_uri"] = "<REDACTED_CAMERA_URI>"
    for k in ("storage_dir", "logs_dir", "audit_dir", "calibration_dir"):
        data[k] = str(data[k])

    manifest = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": platform.python_version(),
        "machine": platform.machine(),
        "system": platform.system(),
        "yolo_model_sha256": _file_sha256_short(cfg.yolo_model_path),
        "landmarker_sha256": _file_sha256_short(cfg.landmarker_path),
        "effective_config": data,
    }
    out = cfg.logs_dir / "runtime_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
