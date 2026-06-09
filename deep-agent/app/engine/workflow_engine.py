"""流程引擎：驱动工作项按阶段流转。"""
from __future__ import annotations

import asyncio
import contextlib
import enum
from datetime import datetime, timezone
from typing import Optional

from app.agents.base import StageContext, StageResult
from app.agents.pool import expert_pool
from app.agents.experts import create_agent_for_stage
from app.config import settings
from app.db import repository as repo
from app.db.models import (
    ParentTaskStatus,
    StageName,
    StageStatus,
    WorkflowTaskStatus,
)
from app.db.session import session_scope
from app.infra import event_bus
from app.infra.bus import Events
from app.infra.queues import WorkflowQueueItem, workflow_queue
from app.logging import logger


class StageAction(enum.Enum):
    CONTINUE = "continue"
    RETRY = "retry"
    WAIT_CONFIRM = "wait_confirm"
    ABORT = "abort"


# 哪些阶段是评审类（其结果决定是否通过/打回）
_REVIEW_STAGES: set[StageName] = {
    StageName.REQUIREMENT_REVIEW,
    StageName.TECHNICAL_REVIEW,
    StageName.CODE_REVIEW,
}

# 需要用户确认后才能推进的阶段
_REQUIREMENT_CONFIRM_STAGES: set[StageName] = {
    StageName.REQUIREMENT_REVIEW,   # 需求评审通过后需用户确认清单
}

# 评审类阶段对应的被评审阶段（用于交叉评审）
_REVIEW_TARGET_MAP: dict[StageName, StageName] = {
    StageName.REQUIREMENT_REVIEW: StageName.REQUIREMENT_ANALYSIS,
    StageName.TECHNICAL_REVIEW: StageName.TECHNICAL_DESIGN,
    StageName.CODE_REVIEW: StageName.IMPLEMENTATION,
}


def _is_review(stage_name: str | StageName) -> bool:
    if isinstance(stage_name, str):
        try:
            stage_name = StageName(stage_name)
        except ValueError:
            return False
    return stage_name in _REVIEW_STAGES


def _needs_user_confirm(stage_name: StageName) -> bool:
    """某些评审阶段通过后，需要用户主动确认需求清单才能推进。"""
    return stage_name in _REQUIREMENT_CONFIRM_STAGES


class WorkflowEngine:
    """流程引擎：常驻协程消费 workflow_queue，驱动每个工作项走完模板。"""

    def __init__(self, max_concurrent: int = 4) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._stop_event = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

    async def run_forever(self) -> None:
        """主循环：从 workflow_queue 取任务派发。"""
        logger.info("流程引擎启动 (max_concurrent={})", self._semaphore._value)
        while not self._stop_event.is_set():
            try:
                item: WorkflowQueueItem = await asyncio.wait_for(
                    workflow_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            task = asyncio.create_task(self._handle_item(item))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        # 等待所有任务结束
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("流程引擎已停止")

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # 单个工作项的处理
    # ------------------------------------------------------------------

    async def _handle_item(self, item: WorkflowQueueItem) -> None:
        async with self._semaphore:
            await self._process_workflow(item.workflow_id)

    async def _process_workflow(self, workflow_id: str) -> None:
        """把一个工作项跑完整个模板。"""
        async with session_scope() as session:
            wf = await repo.get_workflow_task(session, workflow_id)
            if wf is None:
                logger.warning("工作项不存在: {}", workflow_id)
                return
            parent = await repo.get_parent_task(session, wf.parent_id)
            parent_desc = parent.description if parent else ""
            stages_snapshot = [
                {
                    "id": s.id,
                    "name": s.name,
                    "order_index": s.order_index,
                    "output": s.output,
                    "review_comment": s.review_comment,
                    "status": s.status,
                    "expert_type": s.expert_type,
                }
                for s in wf.stages
            ]

        # 切到 IN_PROGRESS
        async with session_scope() as session:
            await repo.update_workflow_status(
                session,
                workflow_id,
                status=WorkflowTaskStatus.IN_PROGRESS,
                started_at=datetime.now(timezone.utc),
                heartbeat_at=datetime.now(timezone.utc),
            )
            await repo.add_log(
                session,
                f"工作项开始执行: {wf.title}",
                workflow_id=workflow_id,
                parent_id=wf.parent_id,
            )
        await event_bus.publish(
            Events.WORKFLOW_STATUS,
            {"workflow_id": workflow_id, "status": WorkflowTaskStatus.IN_PROGRESS.value},
        )

        # 顺序执行各阶段
        for stage_info in stages_snapshot:
            stage_id = stage_info["id"]
            stage_name = stage_info["name"]
            expert_type = stage_info.get("expert_type", "developer")
            # 重新读最新状态
            async with session_scope() as session:
                latest = await repo.get_workflow_task(session, workflow_id)
                if latest is None or latest.status == WorkflowTaskStatus.CANCELLED:
                    logger.info("工作项被取消: {}", workflow_id)
                    return
                stage = next((s for s in latest.stages if s.id == stage_id), None)
                if stage is None:
                    continue
                if stage.status not in (StageStatus.PENDING, StageStatus.FAILED):
                    continue

            action = await self._run_stage(
                workflow_id=workflow_id,
                stage_id=stage_id,
                stage_name=stage_name,
                expert_type=expert_type,
                workflow_title=wf.title,
                workflow_description=wf.description,
                parent_description=parent_desc,
            )
            if action == StageAction.RETRY:
                # 失败处理：累计 retries
                async with session_scope() as session:
                    latest = await repo.get_workflow_task(session, workflow_id)
                    if latest is None:
                        return
                    new_retries = latest.retries + 1
                    if new_retries > settings.max_retries:
                        await repo.update_workflow_status(
                            session,
                            workflow_id,
                            status=WorkflowTaskStatus.FAILED,
                            finished_at=datetime.now(timezone.utc),
                            last_error=f"达到最大重试次数 {settings.max_retries}",
                        )
                        await repo.add_log(
                            session,
                            f"工作项失败: 重试耗尽",
                            workflow_id=workflow_id,
                            parent_id=wf.parent_id,
                            level="error",
                        )
                        await event_bus.publish(
                            Events.WORKFLOW_STATUS,
                            {
                                "workflow_id": workflow_id,
                                "status": WorkflowTaskStatus.FAILED.value,
                            },
                        )
                        await _refresh_parent_status(session, wf.parent_id)
                        return
                    await repo.update_workflow_status(
                        session,
                        workflow_id,
                        retries=new_retries,
                        heartbeat_at=datetime.now(timezone.utc),
                        last_error=f"阶段 {stage_name} 失败，准备重试",
                    )
                # 重新入队
                await workflow_queue.put(
                    WorkflowQueueItem(workflow_id=workflow_id, parent_id=wf.parent_id)
                )
                return

            if action == StageAction.WAIT_CONFIRM:
                # 等待用户确认，不继续处理后续阶段，也不重新入队
                logger.info("阶段 {} 等待用户确认，暂停工作流", stage_name)
                return

        # 全部阶段成功
        async with session_scope() as session:
            await repo.update_workflow_status(
                session,
                workflow_id,
                status=WorkflowTaskStatus.COMPLETED,
                progress=100,
                finished_at=datetime.now(timezone.utc),
            )
            await repo.add_log(
                session,
                f"工作项完成: {wf.title}",
                workflow_id=workflow_id,
                parent_id=wf.parent_id,
            )
        await event_bus.publish(
            Events.WORKFLOW_STATUS,
            {"workflow_id": workflow_id, "status": WorkflowTaskStatus.COMPLETED.value},
        )
        await self._refresh_parent_status_public(wf.parent_id)

    async def _run_stage(
        self,
        *,
        workflow_id: str,
        stage_id: str,
        stage_name: str,
        expert_type: str,
        workflow_title: str,
        workflow_description: str,
        parent_description: str,
    ) -> StageAction:
        """执行单个阶段。返回 StageAction 指示下一步行动。"""
        async with session_scope() as session:
            wf = await repo.get_workflow_task(session, workflow_id)
            if wf is None:
                return StageAction.ABORT
            history = [
                {
                    "artifacts": (s.output or {}),
                    "review_comment": s.review_comment,
                    "name": s.name.value,
                }
                for s in wf.stages
                if s.id != stage_id and s.output
            ]
            stage = next((s for s in wf.stages if s.id == stage_id), None)
            if stage is None:
                return False

        # 标记 RUNNING
        async with session_scope() as session:
            await repo.update_stage(
                session,
                stage_id,
                status=StageStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
            await repo.add_log(
                session,
                f"阶段 {stage_name} 开始 ({expert_type})",
                workflow_id=workflow_id,
                parent_id=wf.parent_id,
            )
        await event_bus.publish(
            Events.STAGE_STATUS,
            {"workflow_id": workflow_id, "stage_id": stage_id, "status": "running"},
        )

        async def heartbeat() -> None:
            async with session_scope() as session:
                await repo.update_workflow_status(
                    session, workflow_id, heartbeat_at=datetime.now(timezone.utc)
                )

        ctx = StageContext(
            workflow=wf,
            stage=stage,
            parent_description=parent_description,
            history=history,
            on_heartbeat=heartbeat,
        )

        try:
            # 使用 create_agent_for_stage 根据阶段类型创建专家
            # 评审类阶段使用 cross_reviewer
            stage_name_enum = StageName(stage_name) if isinstance(stage_name, str) else stage_name
            expert = create_agent_for_stage(stage_name_enum)
        except Exception as e:
            logger.error("创建专家失败: {} {}", stage_name, e)
            await self._mark_stage_failed(workflow_id, stage_id, f"专家创建失败: {e}")
            return StageAction.RETRY

        try:
            result: StageResult = await expert.run(ctx)
        except Exception as e:  # 防御性兜底
            logger.exception("专家执行异常")
            await self._mark_stage_failed(workflow_id, stage_id, str(e))
            return StageAction.RETRY

        if not result.success:
            await self._mark_stage_failed(workflow_id, stage_id, result.summary)
            return StageAction.RETRY

        # 写产物
        async with session_scope() as session:
            await repo.update_stage(
                session,
                stage_id,
                status=StageStatus.SUCCEEDED,
                output={"summary": result.summary, **result.artifacts},
                review_comment=result.review_comment,
                finished_at=datetime.now(timezone.utc),
            )
            # 更新进度（粗略：已完成的阶段数 / 总阶段数）
            wf2 = await repo.get_workflow_task(session, workflow_id)
            if wf2 is not None:
                total = len(wf2.stages)
                done = sum(1 for s in wf2.stages if s.status == StageStatus.SUCCEEDED)
                progress = int(done / max(total, 1) * 100)
                await repo.update_workflow_status(
                    session,
                    workflow_id,
                    progress=progress,
                    heartbeat_at=datetime.now(timezone.utc),
                )
            await repo.add_log(
                session,
                f"阶段 {stage_name} 完成: {result.summary}",
                workflow_id=workflow_id,
                parent_id=wf.parent_id,
                data={"artifacts": result.artifacts, "review_comment": result.review_comment},
            )

        # 评审类阶段的特殊处理
        is_review = _is_review(stage_name)
        if is_review:
            approved = result.approved
            if approved is None:
                approved = "通过" in result.summary or "approve" in result.summary.lower()
            if not approved:
                # 评审未通过：打回重做（重置评审阶段和被评审阶段为 PENDING）
                async with session_scope() as session:
                    target_stage_name = _REVIEW_TARGET_MAP.get(StageName(stage_name))
                    wf_for_reset = await repo.get_workflow_task(session, workflow_id)
                    if wf_for_reset:
                        for s in wf_for_reset.stages:
                            if s.name == target_stage_name and s.status == StageStatus.SUCCEEDED:
                                await repo.update_stage(session, s.id, status=StageStatus.PENDING)
                                logger.info("评审打回：重置 {} 为 PENDING", s.name)
                    await repo.update_stage(
                        session,
                        stage_id,
                        status=StageStatus.PENDING,
                        review_comment=result.review_comment or "评审未通过",
                    )
                await event_bus.publish(
                    Events.STAGE_REVIEW_NEEDED,
                    {
                        "workflow_id": workflow_id,
                        "stage_id": stage_id,
                        "comment": result.review_comment or "评审未通过",
                    },
                )
                return StageAction.RETRY

        # 需求清单确认门控：requirement_review 通过后，需用户主动确认清单才能推进
        stage_name_enum = StageName(stage_name) if isinstance(stage_name, str) else stage_name
        if _needs_user_confirm(stage_name_enum) and (result.approved is True):
            # 发布需求清单确认事件，前端显示清单供用户编辑确认
            req_items = result.artifacts.get("items", [])
            async with session_scope() as session:
                await repo.update_stage(
                    session,
                    stage_id,
                    status=StageStatus.NEEDS_REVIEW,
                    requirement_items=req_items,
                )
            await event_bus.publish(
                Events.STAGE_REVIEW_NEEDED,
                {
                    "workflow_id": workflow_id,
                    "stage_id": stage_id,
                    "stage_name": stage_name,
                    "requirement_items": req_items,
                    "message": "需求清单待确认，请编辑并勾选确认",
                },
            )
            return StageAction.WAIT_CONFIRM

        await event_bus.publish(
            Events.STAGE_STATUS,
            {
                "workflow_id": workflow_id,
                "stage_id": stage_id,
                "status": StageStatus.SUCCEEDED.value,
            },
        )
        return StageAction.CONTINUE

    async def _mark_stage_failed(
        self, workflow_id: str, stage_id: str, reason: str
    ) -> None:
        async with session_scope() as session:
            await repo.update_stage(
                session, stage_id, status=StageStatus.FAILED, review_comment=reason
            )
            await repo.add_log(
                session,
                f"阶段失败: {reason}",
                workflow_id=workflow_id,
                level="error",
            )
        await event_bus.publish(
            Events.STAGE_STATUS,
            {
                "workflow_id": workflow_id,
                "stage_id": stage_id,
                "status": StageStatus.FAILED.value,
                "reason": reason,
            },
        )

    async def _refresh_parent_status_public(self, parent_id: str) -> None:
        async with session_scope() as session:
            await _refresh_parent_status(session, parent_id)


async def _refresh_parent_status(session, parent_id: str) -> None:
    """根据子工作项状态聚合父任务状态。"""
    parent = await repo.get_parent_task(session, parent_id)
    if parent is None:
        return
    if not parent.workflow_tasks:
        return
    statuses = {w.status for w in parent.workflow_tasks}
    if all(s == WorkflowTaskStatus.COMPLETED for s in statuses):
        new_status = ParentTaskStatus.COMPLETED
    elif any(s == WorkflowTaskStatus.FAILED for s in statuses):
        new_status = ParentTaskStatus.FAILED
    elif any(s == WorkflowTaskStatus.IN_PROGRESS for s in statuses):
        new_status = ParentTaskStatus.IN_PROGRESS
    else:
        new_status = ParentTaskStatus.SCHEDULED
    if parent.status != new_status:
        await repo.update_parent_status(session, parent_id, new_status)
        await event_bus.publish(
            Events.PARENT_STATUS,
            {"parent_id": parent_id, "status": new_status.value},
        )
