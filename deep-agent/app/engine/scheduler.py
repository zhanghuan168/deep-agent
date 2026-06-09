"""任务调度器：消费父任务队列，拆分工作项。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.db import repository as repo
from app.db.models import (
    ParentTaskStatus,
    WorkflowTaskStatus,
)
from app.db.session import session_scope
from app.infra import event_bus
from app.infra.bus import Events
from app.infra.queues import (
    SchedulerQueueItem,
    WorkflowQueueItem,
    scheduler_queue,
    workflow_queue,
)
from app.logging import logger
from app.pm import planner


class Scheduler:
    """常驻协程，从 scheduler_queue 中取待规划的父任务。"""

    def __init__(self) -> None:
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        logger.info("任务调度器启动")
        while not self._stop_event.is_set():
            try:
                item: SchedulerQueueItem = await asyncio.wait_for(
                    scheduler_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            try:
                await self._schedule(item.parent_id)
            except Exception:
                logger.exception("调度失败: parent_id={}", item.parent_id)
        logger.info("任务调度器已停止")

    def stop(self) -> None:
        self._stop_event.set()

    async def _schedule(self, parent_id: str) -> None:
        """调用规划器拆解父任务为子工作项。"""
        async with session_scope() as session:
            parent = await repo.get_parent_task(session, parent_id)
            if parent is None:
                logger.warning("父任务不存在: {}", parent_id)
                return
            title = parent.title
            description = parent.description
            existing_plan = parent.plan

        plan = existing_plan or await planner.make_plan(title, description)

        # 先在一个事务里把父任务置为 SCHEDULED 并创建所有工作项
        # （commit 后再入队，避免引擎协程读到未提交数据）
        created_ids: list[str] = []
        async with session_scope() as session:
            await repo.update_parent_status(
                session,
                parent_id,
                status=ParentTaskStatus.SCHEDULED,
                plan=plan,
            )
            await repo.add_log(
                session,
                f"父任务已规划，包含 {len(plan.get('work_items', []))} 个子工作项",
                parent_id=parent_id,
            )

            for item in plan.get("work_items", []):
                wt = await repo.create_workflow_task(
                    session,
                    parent_id=parent_id,
                    title=item.get("title", "未命名"),
                    description=item.get("description", ""),
                    priority=int(item.get("priority", 5)),
                    inputs=item.get("inputs"),
                )
                created_ids.append(wt.id)
        # 事务已 commit，安全入队
        for wf_id in created_ids:
            await event_bus.publish(
                Events.WORKFLOW_CREATED,
                {
                    "workflow_id": wf_id,
                    "parent_id": parent_id,
                },
            )
            await workflow_queue.put(
                WorkflowQueueItem(workflow_id=wf_id, parent_id=parent_id)
            )

        await event_bus.publish(
            Events.PARENT_SCHEDULED, {"parent_id": parent_id}
        )
        logger.info("父任务已拆解并入队: {} ({} 项)", parent_id, len(created_ids))
