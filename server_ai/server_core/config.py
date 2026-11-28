"""Central configuration for Server of AI (e:/AI/PI_AI/server_ai)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SERVER_DATA_DIR", BASE_DIR / "data"))
EVIDENCE_DIR = Path(os.environ.get("SERVER_EVIDENCE_DIR", BASE_DIR / "evidence"))
HARD_NEGATIVES_DIR = DATA_DIR / "hard_negatives"
DB_PATH = DATA_DIR / "central_server.db"
STATIC_DIR = BASE_DIR / "static"

DATA_DIR.mkdir(parents=True, exist_ok=True)
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
HARD_NEGATIVES_DIR.mkdir(parents=True, exist_ok=True)

# Environment profile: 'prod' (strict boot, no default tokens) vs 'lab' (localhost-only bootstrap)
ENV_PROFILE = os.environ.get("SERVER_ENV_PROFILE", "prod").strip().lower()
_RAW_HTTP_HOST = os.environ.get("SERVER_HTTP_HOST", "127.0.0.1" if ENV_PROFILE == "lab" else "0.0.0.0").strip()
# LCK-06: lab profile with bootstrap tokens MUST ONLY bind loopback (never 0.0.0.0!)
HTTP_HOST = "127.0.0.1" if (ENV_PROFILE == "lab" and _RAW_HTTP_HOST not in ("127.0.0.1", "::1", "localhost")) else _RAW_HTTP_HOST
HTTP_PORT = int(os.environ.get("SERVER_HTTP_PORT", 5000))
UDP_DISCOVERY_PORT = int(os.environ.get("SERVER_UDP_PORT", 5005))
UDP_MAGIC_REQUEST_ACCEPTED = {"IOTV3_DISCOVER_SERVER", "DISCOVER_IOT_SERVER", "PI_AI_DISCOVER_SERVER"}
UDP_MAGIC_RESPONSE = "IOTV3_SERVER_BEACON"
SERVICE_NAME = "pi_ai_central_server"

# Hard body size limits (prevents OOM DoS)
MAX_JSON_BODY_BYTES = int(os.environ.get("MAX_JSON_BODY_BYTES", 2 * 1024 * 1024))       # 2 MB
MAX_UPLOAD_BODY_BYTES = int(os.environ.get("MAX_UPLOAD_BODY_BYTES", 15 * 1024 * 1024)) # 15 MB
MAX_BURST_FRAMES = int(os.environ.get("MAX_BURST_FRAMES", 60))

# RBAC - 5 roles
ROLES = ["viewer", "driver_phone", "device", "operator", "admin"]
TOKEN_HEADER_CANDIDATES = [
    ("header", "authorization"),   # Bearer <token>
    ("header", "x-api-token"),
    ("header", "x-dashboard-token"),
    ("header", "x-auth-token"),    # ESP32 & Pi
    ("cookie", "web_token"),
    ("query", "token"),            # SSE / EventSource only
]

# ONLY used when SERVER_ENV_PROFILE == "lab"
LAB_BOOTSTRAP_TOKENS = {
    "lab-admin-token": "admin",
    "lab-operator-token": "operator",
    "lab-viewer-token": "viewer",
    "lab-device-token": "device",
    "lab-driverphone-token": "driver_phone",
}

# Hardware & Watchdog thresholds
PI_THERMAL_WARN_C = 58.0
PI_THERMAL_CRIT1_C = 75.0
PI_THERMAL_CRIT2_C = 80.0

MQ135_WARN_PPM = 1000.0
MQ135_CRIT_PPM = 1500.0

PI_OFFLINE_WATCHDOG_S = float(os.environ.get("PI_OFFLINE_WATCHDOG_S", 10.0))
ENABLE_NO_FACE_TAMPER_ALERT = os.environ.get("ENABLE_NO_FACE_TAMPER_ALERT", "0").strip().lower() in ("1", "true", "yes")
CAMERA_NO_FACE_TAMPER_S = float(os.environ.get("CAMERA_NO_FACE_TAMPER_S", 30.0))
TAMPER_MIN_SPEED_KMH = float(os.environ.get("TAMPER_MIN_SPEED_KMH", 15.0))
INCIDENT_COOLDOWN_S = float(os.environ.get("INCIDENT_COOLDOWN_S", 120.0))

# Mute / Alert dismissal
MUTE_DURATION_DEFAULT_S = 30
MUTE_ABUSE_COUNT_THRESHOLD = 3
MUTE_ABUSE_WINDOW_S = 600
MUTE_ABUSE_SPEED_THRESHOLD_KMH = 30.0
MUTE_ABUSE_RECURRENCE_WINDOW_S = 15.0
REACTION_TIME_FAST_MS = 1500
REACTION_TIME_SLOW_MS = 4000
DISMISS_SOURCES = {"PHYSICAL_MUTE_BUTTON", "PHONE_BLE", "STEERING_BLE", "REMOTE_OPERATOR"}

# Trip & Safety Score (covers both Pi canonical alert types and raw YOLO classes)
SAFETY_SCORE_BASE = 100.0
SAFETY_SCORE_PENALTY = {
    "DROWSY": 6.0,
    "DROWSY_ALERT": 6.0,
    "Eye_Closed": 6.0,
    "YAWNING": 2.5,
    "YAWNING_ALERT": 2.5,
    "Mouth_Open": 2.0,
    "DISTRACTED": 3.5,
    "DISTRACTED_ALERT": 3.5,
    "Distracted Head": 3.5,
    "SMOKING": 5.0,
    "Smoking": 5.0,
    "PHONE": 5.0,
    "phone": 5.0,
    "MUTE_ABUSE": 10.0,
    "CO2_CRIT": 0.0,
    "AUTO_CO2_CRIT": 0.0,
    "CO2_WARN": 0.0,
    "AUTO_CO2_WARN": 0.0,
    "ESP_BROWNOUT": 0.0,
    "ESP_HUB_BROWNOUT": 0.0,
    "SOS": 0.0,
    "DRIVER_ABSENT": 0.0,
    "MONITORING_AUTO_RESUMED_BY_SPEED": 0.0,
    "DROWSY_LEVEL3_ALARM": 0.0,
    "DROWSY_RELAPSE_CONTINUOUS": 0.0,
    "HARSH_BRAKE": 3.0,
    "TAMPER": 10.0,
}
# Advisory / environmental / emergency alerts: warn driver & log incident, NEVER deduct driver Safety Score
NON_PENALTY_ALERT_TYPES = frozenset({
    "SOS",
    "ESP_BROWNOUT",
    "ESP_HUB_BROWNOUT",
    "AUTO_CO2_CRIT",
    "AUTO_CO2_WARN",
    "CO2_CRIT",
    "CO2_WARN",
    "DRIVER_ABSENT",
    "MONITORING_AUTO_RESUMED_BY_SPEED",
    "DROWSY_LEVEL3_ALARM",
    "DROWSY_RELAPSE_CONTINUOUS",
})
TRIP_IDLE_TIMEOUT_MIN = float(os.environ.get("TRIP_IDLE_TIMEOUT_MIN", 15.0))
TRIP_MOVING_SPEED_KMH = float(os.environ.get("TRIP_MOVING_SPEED_KMH", 3.0))

# Retention
TELEMETRY_HISTORY_RETENTION_DAYS = int(os.environ.get("TELEMETRY_RETENTION_DAYS", 30))
EVIDENCE_MAX_SIZE_GB = float(os.environ.get("EVIDENCE_MAX_SIZE_GB", 20.0))
CLEANUP_INTERVAL_S = float(os.environ.get("CLEANUP_INTERVAL_S", 3600.0))

# Command whitelist
COMMAND_WHITELIST_PREFIXES = ("VOICE:", "ALERT:", "VOL:", "ALARM:")
COMMAND_WHITELIST_EXACT = {"STOP", "MUTE", "PING", "WHOAMI"}
COMMAND_WHITELIST_NEW = {
    "BT_UNPAIR",
    "BT_FORCE_RESCAN",
    "CONFIG_SET",
    "REST_BREAK_REQUEST",
    "CALIBRATE",
    "DISMISS_DROWSY_ALARM",
    "MODE_AUTO",
    "MODE_PAUSE_30M",
    "MODE_REST_OFF",
    "ALARM:ON",
    "ALARM:CONT",
    "ALARM:OFF",
}

# Tier-2 Deep CNN Verifier (mode: auto | heuristic | onnx | b1)
TIER2_VERIFIER_MODE = os.environ.get("TIER2_VERIFIER_MODE", "auto").strip().lower()
DEEP_CNN_MODEL_PATH = os.environ.get("DEEP_CNN_MODEL_PATH", "").strip()
DEEP_VERIFIER_WORKERS = int(os.environ.get("DEEP_VERIFIER_WORKERS", 2))
SERVER_STITCH_MP4 = os.environ.get("SERVER_STITCH_MP4", "1") != "0"

# Optional PBL4_HTTM Node.js Bridge (A2: disabled by default unless PBL4_BACKEND_URL is set)
PBL4_BACKEND_URL = os.environ.get("PBL4_BACKEND_URL", "").strip()
PBL4_DEVICE_TOKEN = os.environ.get("PBL4_DEVICE_TOKEN", "").strip()
PBL4_DEVICE_ID = os.environ.get("PBL4_DEVICE_ID", "").strip()
PBL4_TRIP_ID = os.environ.get("PBL4_TRIP_ID", "").strip()
PBL4_SMOKING_CODE = os.environ.get("PBL4_SMOKING_CODE", "PHONE_DISTRACTION").strip()
PBL4_TELEMETRY_INTERVAL_S = float(os.environ.get("PBL4_TELEMETRY_INTERVAL_S", 3.0))
PBL4_HEARTBEAT_INTERVAL_S = float(os.environ.get("PBL4_HEARTBEAT_INTERVAL_S", 30.0))
PBL4_FORWARD_NON_PENALTY = os.environ.get("PBL4_FORWARD_NON_PENALTY", "0") == "1"

# Webhook
WEBHOOK_URL = os.environ.get("SERVER_WEBHOOK_URL", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
