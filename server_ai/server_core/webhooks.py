"""Truly asynchronous webhook dispatcher (background thread queue, never blocks FastAPI event loop)."""
from __future__ import annotations

import json
import queue
import threading
import urllib.request
from typing import Dict

from server_ai.server_core import config

_queue: queue.Queue = queue.Queue(maxsize=100)
_started = False
_lock = threading.Lock()


def _worker() -> None:
    while True:
        item = _queue.get()
        if item is None:
            _queue.task_done()
            break
        title, detail = item
        try:
            url = config.WEBHOOK_URL
            if not url:
                continue
            text = f"[CANH BAO] {title}\n{json.dumps(detail, ensure_ascii=False)[:1500]}"
            if "api.telegram.org" in url:
                payload: Dict[str, object] = {"text": text}
                if config.TELEGRAM_CHAT_ID:
                    payload["chat_id"] = config.TELEGRAM_CHAT_ID
                body = json.dumps(payload).encode("utf-8")
            elif "discord.com/api/webhooks" in url:
                body = json.dumps({"content": text}).encode("utf-8")
            else:
                body = json.dumps({"title": title, "detail": detail}, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=4):
                pass
        except Exception:
            pass
        finally:
            _queue.task_done()


def _ensure_started() -> None:
    global _started
    with _lock:
        if not _started:
            threading.Thread(target=_worker, name="webhook-sender", daemon=True).start()
            _started = True


def send_webhook(title: str, detail: dict) -> None:
    if not config.WEBHOOK_URL:
        return
    _ensure_started()
    try:
        _queue.put_nowait((title, detail))
    except queue.Full:
        pass
