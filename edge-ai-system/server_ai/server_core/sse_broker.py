"""Thread-safe SSE broker (uses threading.Lock + loop.call_soon_threadsafe + clean shutdown sentinel)."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import OrderedDict
from typing import AsyncGenerator, Optional

MAX_SSE_SUBSCRIBERS = 100


class SSEBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: OrderedDict[asyncio.Queue, None] = OrderedDict()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._loop = loop

    async def subscribe(self) -> asyncio.Queue:
        running = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        evicted: Optional[asyncio.Queue] = None
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                self._loop = running
            if len(self._subscribers) >= MAX_SSE_SUBSCRIBERS:
                evicted, _ = self._subscribers.popitem(last=False)
            self._subscribers[q] = None
        if evicted is not None:
            # Push None sentinel so evicted stream terminates cleanly (no zombie socket leak!)
            self._put_safe(evicted, None)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(q, None)

    def _put_safe(self, q: asyncio.Queue, payload: Optional[dict]) -> None:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            if payload is None:
                # Never drop shutdown/eviction sentinel on a full queue (fixes F-025 zombie stream!)
                try:
                    q.get_nowait()
                    q.put_nowait(None)
                except Exception:
                    pass

    def publish(self, event_type: str, data: dict) -> None:
        payload = {"type": event_type, "ts": time.time(), "data": data}
        with self._lock:
            subs = list(self._subscribers)
            loop = self._loop
        if not subs:
            return
        if loop is not None and not loop.is_closed() and loop.is_running():
            for q in subs:
                try:
                    loop.call_soon_threadsafe(self._put_safe, q, payload)
                except RuntimeError:
                    pass
        else:
            for q in subs:
                self._put_safe(q, payload)

    def shutdown(self) -> None:
        with self._lock:
            subs = list(self._subscribers)
            loop = self._loop
        for q in subs:
            if loop is not None and not loop.is_closed() and loop.is_running():
                try:
                    loop.call_soon_threadsafe(self._put_safe, q, None)
                except RuntimeError:
                    pass
            else:
                self._put_safe(q, None)

    async def stream(self) -> AsyncGenerator[str, None]:
        q = await self.subscribe()
        try:
            yield f"event: hello\ndata: {json.dumps({'ok': True})}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    if item is None:
                        break
                    yield f"event: {item['type']}\ndata: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            await self.unsubscribe(q)


broker = SSEBroker()
