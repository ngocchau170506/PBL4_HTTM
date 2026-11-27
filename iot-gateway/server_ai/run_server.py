"""Entrypoint for PI_AI Server of AI (HTTP FastAPI + Offline-safe UDP Discovery :5005)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from server_ai.server_core import auth, config, database, udp_discovery


def main() -> None:
    database.init_db()
    auth.validate_boot_tokens()
    udp_discovery.start()
    print(
        f"[PI_AI Server of AI] profile={config.ENV_PROFILE} "
        f"HTTP=http://{config.HTTP_HOST}:{config.HTTP_PORT} UDP=:{config.UDP_DISCOVERY_PORT}"
    )
    uvicorn.run("server_ai.api_server:app", host=config.HTTP_HOST, port=config.HTTP_PORT, log_level="info")


if __name__ == "__main__":
    main()
