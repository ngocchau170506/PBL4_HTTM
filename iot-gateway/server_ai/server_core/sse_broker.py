"""Thread-safe SSE broker (uses loop.call_soon_threadsafe from sync worker threads)."""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator, Optional, Set


class SSEBroker:
    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def subscribe(self) -> asyncio.Queue:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _put_safe(self, q: asyncio.Queue, payload: dict) -> None:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass

    def publish(self, event_type: str, data: dict) -> None:
        payload = {"type": event_type, "ts": time.time(), "data": data}
        subs = list(self._subscribers)
        if not subs:
            return
        loop = self._loop
        if loop is not None and loop.is_running():
            for q in subs:
                try:
                    loop.call_soon_threadsafe(self._put_safe, q, payload)
                except RuntimeError:
                    pass
        else:
            for q in subs:
                self._put_safe(q, payload)

    async def stream(self) -> AsyncGenerator[str, None]:
        q = await self.subscribe()
        try:
            yield f"event: hello\ndata: {json.dumps({'ok': True})}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: {item['type']}\ndata: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            await self.unsubscribe(q)


broker = SSEBroker()
