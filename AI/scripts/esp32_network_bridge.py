"""Cầu nối tương thích: `AI.scripts.esp32_network_bridge` -> `src/fd_response/esp32_network_bridge.py`.

Bản gốc nằm ở `src/fd_response/esp32_network_bridge.py`. `run_gps_standalone` import theo
đường dẫn `AI.scripts.esp32_network_bridge` nên cần file này để chế độ GPS_ONLY chạy được.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fd_response.esp32_network_bridge import *  # noqa: E402,F401,F403
