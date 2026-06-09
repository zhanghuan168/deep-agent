"""自研任务队列。"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.config import settings
from app.logging import logger


@dataclass
class WorkflowQueueItem:
    """工作项队列的载荷：交给流程引擎执行。"""

    workflow_id: str
    parent_id: str
    enqueued_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    enqueue_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class StageQueueItem:
    """阶段队列的载荷：流程引擎内部使用。"""

    workflow_id: str
    stage_id: str
    enqueue_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class SchedulerQueueItem:
    """调度队列的载荷：待规划的父任务。"""

    parent_id: str
    enqueue_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class TaskQueue:
    """基于 asyncio.Queue 的异步任务队列。"""

    def __init__(self, name: str, max_size: int | None = None) -> None:
        self.name = name
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size or settings.queue_max_size)
        self._size_metric: int = 0

    @property
    def size(self) -> int:
        return self._queue.qsize()

    async def put(self, item) -> None:
        await self._queue.put(item)
        logger.debug("队列 {} 入队: {}", self.name, getattr(item, "enqueue_id", item))

    async def get(self):
        item = await self._queue.get()
        try:
            return item
        finally:
            self._queue.task_done()

    async def drain(self) -> list:
        """排空队列（仅在关闭时使用）。"""
        items = []
        while not self._queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                items.append(self._queue.get_nowait())
        return items


# ---------------------------------------------------------------------------
# 全局队列实例
# ---------------------------------------------------------------------------

scheduler_queue: TaskQueue = TaskQueue("scheduler")
workflow_queue: TaskQueue = TaskQueue("workflow")
stage_queue: TaskQueue = TaskQueue("stage")
