"""Cầu nối tương thích: `AI.scripts.fd_frame` -> `src/fd_connect/fd_frame.py`.

Bản gốc nằm ở `src/fd_connect/fd_frame.py`. Một số module (esp_communication, run_gps_standalone)
import theo đường dẫn `AI.scripts.fd_frame` nên cần file này để không bị ModuleNotFoundError.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fd_connect.fd_frame import *  # noqa: E402,F401,F403
