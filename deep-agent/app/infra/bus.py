"""事件总线（观察者模式）。"""
from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from typing import Any, Awaitable, Callable, DefaultDict, Iterable

from app.logging import logger


EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class EventBus:
    """极简的发布订阅总线。"""

    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, list[EventCallback]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, event_type: str, callback: EventCallback) -> None:
        self._subscribers[event_type].append(callback)
        logger.debug("事件订阅: {} -> {}", event_type, callback)

    def unsubscribe(self, event_type: str, callback: EventCallback) -> None:
        if callback in self._subscribers.get(event_type, []):
            self._subscribers[event_type].remove(callback)

    def subscribers(self, event_type: str) -> Iterable[EventCallback]:
        return list(self._subscribers.get(event_type, []))

    async def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """发布事件。所有订阅者都会被调用（即使抛错也不影响其他订阅者）。"""
        data = data or {}
        callbacks = self._subscribers.get(event_type, [])
        if not callbacks:
            return
        logger.debug("事件发布: {} ({} 个订阅者)", event_type, len(callbacks))
        for cb in callbacks:
            try:
                result = cb(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # pragma: no cover - 防止单点失败
                logger.exception("事件回调 {} 执行失败", cb)


# 事件类型常量
class Events:
    PARENT_CREATED = "parent.created"
    PARENT_CONFIRMED = "parent.confirmed"
    PARENT_SCHEDULED = "parent.scheduled"
    PARENT_STATUS = "parent.status"
    WORKFLOW_CREATED = "workflow.created"
    WORKFLOW_STATUS = "workflow.status"
    WORKFLOW_PROGRESS = "workflow.progress"
    WORKFLOW_LOG = "workflow.log"
    STAGE_STATUS = "stage.status"
    STAGE_REVIEW_NEEDED = "stage.review_needed"
    CHAT_MESSAGE = "chat.message"
    SYSTEM = "system"


# 全局单例
event_bus = EventBus()
