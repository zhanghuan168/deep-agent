"""WebSocket 连接管理 + 事件分发。"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Set

from fastapi import WebSocket

from app.logging import logger


class WebSocketHub:
    """维护一组 WebSocket 连接并向它们广播事件。"""

    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        logger.info("WebSocket 接入，当前连接数: {}", len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)
        logger.info("WebSocket 断开，当前连接数: {}", len(self._connections))

    async def broadcast(self, event: str, data: dict) -> None:
        if not self._connections:
            return
        payload = {"event": event, "data": data}
        # 拷贝一份以避免迭代过程中修改
        async with self._lock:
            conns = list(self._connections)
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)


hub = WebSocketHub()
