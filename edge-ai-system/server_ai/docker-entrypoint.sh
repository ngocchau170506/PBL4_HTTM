#!/bin/sh
set -e

DATA_DIR="${SERVER_DATA_DIR:-/app/server_ai/data}"
EVIDENCE_DIR="${SERVER_EVIDENCE_DIR:-/app/server_ai/evidence}"
mkdir -p "$DATA_DIR" "$EVIDENCE_DIR"

TOKENS_FILE="$DATA_DIR/tokens.json"
if [ ! -f "$TOKENS_FILE" ] && [ "${SERVER_ENV_PROFILE:-prod}" = "prod" ]; then
  echo "[server-ai] Bootstrapping initial tokens.json in $DATA_DIR..."
  python - <<'PY'
import json
import os
from pathlib import Path

data_dir = Path(os.environ.get("SERVER_DATA_DIR", "/app/server_ai/data"))
tokens_path = data_dir / "tokens.json"

store = {
    "schema_version": "2.0",
    "active": {
        os.environ.get("SERVER_ADMIN_TOKEN", "pbl4_server_ai_admin_token_2026_dev"): "admin",
        os.environ.get("SERVER_OPERATOR_TOKEN", "pbl4_server_ai_operator_token_2026_dev"): "operator",
        os.environ.get("SERVER_DEVICE_TOKEN", "pbl4_server_ai_device_token_2026_dev"): "device",
        os.environ.get("SERVER_VIEWER_TOKEN", "pbl4_server_ai_viewer_token_2026_dev"): "viewer",
        os.environ.get("SERVER_DRIVER_TOKEN", "pbl4_server_ai_driver_token_2026_dev"): "driver_phone",
    },
    "grace": {},
}
tokens_path.write_text(json.dumps(store, indent=2), encoding="utf-8")
os.chmod(tokens_path, 0o600)
PY
fi

exec python server_ai/run_server.py
