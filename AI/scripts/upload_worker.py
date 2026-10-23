"""Non-blocking store-and-forward worker for recorded CNN events."""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Callable, Optional

from AI.scripts.event_storage import EventStorage


class UploadWorker:
    def __init__(
        self,
        storage: EventStorage,
        transport: Optional[Callable[[dict[str, Any], str], bool]] = None,
        retry_base_seconds: float = 2.0,
        retry_max_seconds: float = 300.0,
    ):
        self.storage = storage
        self.transport = transport or self._mock_transport
        self.retry_base_seconds = float(retry_base_seconds)
        self.retry_max_seconds = float(retry_max_seconds)
        self._event_queue: queue.Queue[tuple[dict[str, Any], list[Any]]] = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="cnn-upload-worker", daemon=True)
        self._thread.start()

    def submit_event(self, metadata: dict[str, Any], frames: list[Any]) -> None:
        self._event_queue.put((metadata, frames))
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def close(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise RuntimeError("CNN upload worker did not stop before timeout")

    def _run(self) -> None:
        while True:
            self._save_queued_events()
            if self._stop.is_set() and self._event_queue.empty():
                return
            if self._stop.is_set():
                continue
            for event_id, event_dir, attempts in self.storage.pending():
                try:
                    metadata = json.loads((event_dir / "metadata.json").read_text(encoding="utf-8"))
                    if self.transport(metadata, str(event_dir)):
                        self.storage.mark_uploaded(event_id)
                    else:
                        self._retry(event_id, attempts)
                except (OSError, ValueError, TypeError) as exc:
                    print(f"[CNN UPLOAD] {event_id} deferred: {exc}")
                    self._retry(event_id, attempts)
            self._wake.wait(1.0)
            self._wake.clear()

    def _save_queued_events(self) -> None:
        remaining = self._event_queue.qsize()
        while remaining > 0:
            remaining -= 1
            try:
                metadata, frames = self._event_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self.storage.save_event(metadata, frames)
            except (OSError, ValueError, TypeError) as exc:
                print(f"[CNN STORAGE] {metadata.get('event_id')} deferred: {exc}")
                self._event_queue.put((metadata, frames))
                self._wake.set()
            finally:
                self._event_queue.task_done()

    def _retry(self, event_id: str, attempts: int) -> None:
        next_attempts = attempts + 1
        delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** min(attempts, 8)))
        self.storage.mark_retry(event_id, next_attempts, time.time() + delay)

    @staticmethod
    def _mock_transport(_metadata: dict[str, Any], _event_dir: str) -> bool:
        # MOCK deliberately does not acknowledge or delete real event data.
        return False
