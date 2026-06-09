"""心跳与超时监控。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.config import settings
from app.db import repository as repo
from app.db.models import WorkflowTaskStatus
from app.db.session import session_scope
from app.infra import event_bus
from app.infra.bus import Events
from app.infra.queues import WorkflowQueueItem, workflow_queue
from app.logging import logger


class HeartbeatMonitor:
    """定期扫描 IN_PROGRESS 工作项，标记超时或自动重试。"""

    def __init__(self, interval_seconds: int | None = None) -> None:
        self.interval = interval_seconds or settings.heartbeat_interval_seconds
        self.timeout = timedelta(seconds=settings.workflow_timeout_seconds)
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        logger.info("心跳监控启动 (interval={}s, timeout={}s)", self.interval, self.timeout.total_seconds())
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.interval
                )
                break  # 被 stop
            except asyncio.TimeoutError:
                pass
            try:
                await self._scan_once()
            except Exception:
                logger.exception("心跳扫描异常")
        logger.info("心跳监控已停止")

    def stop(self) -> None:
        self._stop_event.set()

    async def _scan_once(self) -> None:
        now = datetime.now(timezone.utc)
        threshold = now - self.timeout
        async with session_scope() as session:
            # 找出超时工作项
            from sqlalchemy import select

            from app.db.models import WorkflowTask

            stmt = select(WorkflowTask).where(
                WorkflowTask.status == WorkflowTaskStatus.IN_PROGRESS,
                WorkflowTask.heartbeat_at != None,  # noqa: E711
                WorkflowTask.heartbeat_at < threshold,
            )
            overdue = (await session.execute(stmt)).scalars().all()
            for wf in overdue:
                logger.warning(
                    "工作项心跳超时: id={} title={} last_heartbeat={}",
                    wf.id,
                    wf.title,
                    wf.heartbeat_at,
                )
                new_retries = wf.retries + 1
                if new_retries > settings.max_retries:
                    await repo.update_workflow_status(
                        session,
                        wf.id,
                        status=WorkflowTaskStatus.FAILED,
                        last_error="心跳超时，已达最大重试次数",
                    )
                    await repo.add_log(
                        session,
                        "工作项因心跳超时失败",
                        workflow_id=wf.id,
                        parent_id=wf.parent_id,
                        level="error",
                    )
                    await event_bus.publish(
                        Events.WORKFLOW_STATUS,
                        {"workflow_id": wf.id, "status": WorkflowTaskStatus.FAILED.value},
                    )
                else:
                    await repo.update_workflow_status(
                        session,
                        wf.id,
                        retries=new_retries,
                        status=WorkflowTaskStatus.CREATED,
                        last_error="心跳超时，自动重新入队",
                    )
                    await repo.add_log(
                        session,
                        f"心跳超时，自动重试 ({new_retries}/{settings.max_retries})",
                        workflow_id=wf.id,
                        parent_id=wf.parent_id,
                        level="warning",
                    )
                    await event_bus.publish(
                        Events.WORKFLOW_STATUS,
                        {
                            "workflow_id": wf.id,
                            "status": WorkflowTaskStatus.CREATED.value,
                            "reason": "heartbeat_timeout",
                        },
                    )
                    await workflow_queue.put(
                        WorkflowQueueItem(workflow_id=wf.id, parent_id=wf.parent_id)
                    )
