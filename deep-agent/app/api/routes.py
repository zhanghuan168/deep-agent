"""FastAPI 路由：聊天 + 任务查询 + 控制台 API。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws import hub
from app.db import repository as repo
from app.db.models import ConversationRole
from app.db.schemas import (
    ChatMessage,
    ChatRequest,
    ConfirmPlanRequest,
    ParentTaskRead,
    ParentTaskWithChildren,
    ReviewDecision,
    TaskLogRead,
    WorkflowTaskRead,
)
from app.db.session import get_session
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
from app.pm import (
    agent_chat,
    confirm_create_task,
    confirm_start_task,
)


router = APIRouter()


# ---------------------------------------------------------------------------
# 聊天
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=ChatMessage)
async def chat(req: ChatRequest, session: AsyncSession = Depends(get_session)) -> ChatMessage:
    """LLM 驱动的项目管理 agent。

    流程：Agent 优化消息 → LLM 决策（chat 或 ask_to_create）→ 显示 LLM 回复。
    若 LLM 决定创建任务，前端需调用 /api/chat/confirm-create 真正创建。
    """
    reply = await agent_chat(req.message, req.parent_id)
    return ChatMessage(
        role=ConversationRole.PROJECT_MANAGER,
        content=reply.text,
        parent_id=reply.parent_id,
        intent=reply.intent,
        data={"plan": reply.plan},
    )


@router.post("/chat/confirm-create")
async def chat_confirm_create(req: dict, session: AsyncSession = Depends(get_session)) -> ChatMessage:
    """用户确认创建任务：真正调 create_task 工具。"""
    parent_id = req.get("parent_id")
    if not parent_id:
        raise HTTPException(400, "缺少 parent_id")
    reply = await confirm_create_task(parent_id)
    return ChatMessage(
        role=ConversationRole.PROJECT_MANAGER,
        content=reply.text,
        parent_id=reply.parent_id,
        intent=reply.intent,
        data={"plan": reply.plan},
    )


@router.post("/chat/confirm-start")
async def chat_confirm_start(req: dict, session: AsyncSession = Depends(get_session)) -> ChatMessage:
    """用户确认开工：把已创建任务投入调度。"""
    parent_id = req.get("parent_id")
    if not parent_id:
        raise HTTPException(400, "缺少 parent_id")
    reply = await confirm_start_task(parent_id)
    return ChatMessage(
        role=ConversationRole.PROJECT_MANAGER,
        content=reply.text,
        parent_id=reply.parent_id,
        intent=reply.intent,
    )


# ---------------------------------------------------------------------------
# 任务查询
# ---------------------------------------------------------------------------


@router.get("/parents", response_model=list[ParentTaskWithChildren])
async def list_parents(session: AsyncSession = Depends(get_session)) -> list[ParentTaskWithChildren]:
    """返回所有父任务（含完整的层级结构）。"""
    rows = await repo.list_parent_tasks(session)
    result = []
    for parent in rows:
        # selectinload 已预加载 workflow_tasks 和 stages
        # 强制访问以确保关系被加载（避免 MissingGreenlet）
        wfs = list(parent.workflow_tasks)
        for wf in wfs:
            _ = list(wf.stages)
        result.append(parent)
    return result


@router.get("/parents/{parent_id}", response_model=ParentTaskWithChildren)
async def get_parent(parent_id: str, session: AsyncSession = Depends(get_session)):
    parent = await repo.get_parent_task(session, parent_id)
    if parent is None:
        raise HTTPException(404, "父任务不存在")
    return parent


@router.patch("/parents/{parent_id}/status")
async def patch_parent_status(
    parent_id: str,
    payload: dict,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """老板在看板上拖动父任务改状态（draft/confirmed/scheduled/in_progress/blocked/completed/failed）。"""
    from datetime import datetime, timezone

    from app.db.models import ParentTaskStatus
    from app.infra import event_bus
    from app.infra.bus import Events

    new_status = payload.get("status")
    if not new_status:
        raise HTTPException(400, "缺少 status 字段")
    try:
        new_enum = ParentTaskStatus(new_status)
    except ValueError:
        raise HTTPException(400, f"未知 status: {new_status}")
    parent = await repo.get_parent_task(session, parent_id)
    if parent is None:
        raise HTTPException(404, "父任务不存在")
    await repo.update_parent_status(session, parent_id, new_enum)
    await repo.add_log(
        session,
        f"老板在看板把状态改为：{new_enum.value}",
        parent_id=parent_id,
    )
    await session.commit()
    await event_bus.publish(
        Events.PARENT_STATUS,
        {"parent_id": parent_id, "status": new_enum.value},
    )
    return {"ok": True, "status": new_enum.value}


@router.get("/workflows/{workflow_id}", response_model=WorkflowTaskRead)
async def get_workflow(workflow_id: str, session: AsyncSession = Depends(get_session)):
    wf = await repo.get_workflow_task(session, workflow_id)
    if wf is None:
        raise HTTPException(404, "工作项不存在")
    return wf


@router.get("/logs")
async def list_logs(
    parent_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
) -> list[TaskLogRead]:
    rows = await repo.list_logs(
        session, parent_id=parent_id, workflow_id=workflow_id, limit=limit
    )
    return [TaskLogRead.model_validate(r) for r in rows]


@router.get("/conversation/{parent_id}")
async def list_conversation(parent_id: str, session: AsyncSession = Depends(get_session)):
    rows = await repo.list_conversations(session, parent_id)
    return [
        {
            "id": r.id,
            "role": r.role.value,
            "content": r.content,
            "data": r.data,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 评审
# ---------------------------------------------------------------------------


@router.post("/stages/{stage_id}/review")
async def review_stage(
    stage_id: str,
    decision: ReviewDecision,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """人工评审：approve / reject。"""
    from datetime import datetime, timezone

    from app.db import repository as repo
    from app.db.models import StageStatus, WorkflowTaskStatus
    from app.infra.queues import WorkflowQueueItem, workflow_queue

    # 找到对应的 stage / workflow
    from sqlalchemy import select
    from app.db.models import StageInstance

    stmt = select(StageInstance).where(StageInstance.id == stage_id)
    stage = (await session.execute(stmt)).scalar_one_or_none()
    if stage is None:
        raise HTTPException(404, "阶段不存在")
    workflow_id = stage.workflow_id
    approved = decision.decision.lower() == "approve"
    new_status = StageStatus.SUCCEEDED if approved else StageStatus.FAILED
    await repo.update_stage(
        session,
        stage_id,
        status=new_status,
        review_comment=decision.comment,
        finished_at=datetime.now(timezone.utc),
    )
    await repo.add_log(
        session,
        f"人工评审: {decision.decision} - {decision.comment}",
        workflow_id=workflow_id,
        level="info",
    )
    if approved:
        # 重新入队，让流程引擎继续推进
        wf = await repo.get_workflow_task(session, workflow_id)
        if wf is not None:
            await workflow_queue.put(
                WorkflowQueueItem(workflow_id=workflow_id, parent_id=wf.parent_id)
            )
    else:
        await repo.update_workflow_status(
            session,
            workflow_id,
            status=WorkflowTaskStatus.FAILED,
            last_error=f"评审被人工拒绝: {decision.comment}",
        )
    await event_bus.publish(
        Events.STAGE_STATUS,
        {"workflow_id": workflow_id, "stage_id": stage_id, "status": new_status.value},
    )
    return {"ok": True, "approved": approved}


# ---------------------------------------------------------------------------
# 需求清单（requirement_analysis 阶段输出，供前端编辑确认）
# ---------------------------------------------------------------------------


@router.get("/stages/{stage_id}/requirement-items")
async def get_requirement_items(
    stage_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """获取需求清单和确认状态。"""
    items, confirmed = await repo.get_stage_requirement_items(session, stage_id)
    return {"items": items or [], "confirmed": confirmed}


@router.put("/stages/{stage_id}/requirement-items")
async def update_requirement_items(
    stage_id: str,
    req: dict,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """更新需求清单内容（用户编辑后保存）。"""
    items = req.get("items", [])
    await repo.update_stage_requirement_items(session, stage_id, items, confirmed=False)
    await repo.add_log(
        session,
        f"需求清单已更新，共 {len(items)} 条",
        level="info",
    )
    return {"ok": True, "items": items}


@router.post("/stages/{stage_id}/confirm-requirement")
async def confirm_requirement(
    stage_id: str,
    req: dict,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """用户确认需求清单，推进到下一阶段。

    Body: {"items": [...]}  可选，传入编辑后的清单
    """
    from datetime import datetime, timezone
    from app.db.models import StageStatus, StageInstance
    from app.infra.queues import WorkflowQueueItem, workflow_queue

    # 如果用户传了编辑后的清单，先更新
    items = req.get("items")
    if items is not None:
        await repo.update_stage_requirement_items(session, stage_id, items, confirmed=True)

    # 确认需求清单
    ok = await repo.confirm_stage_requirement(session, stage_id)
    if not ok:
        raise HTTPException(400, "阶段不存在或不在待确认状态")

    # 找到 workflow 重新入队
    stage = await session.get(StageInstance, stage_id)
    wf = await repo.get_workflow_task(session, stage.workflow_id)
    if wf:
        await workflow_queue.put(
            WorkflowQueueItem(workflow_id=wf.id, parent_id=wf.parent_id)
        )
        await repo.add_log(
            session,
            "需求清单已确认，工作流继续推进",
            workflow_id=wf.id,
            parent_id=wf.parent_id,
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# 评审模板 CRUD
# ---------------------------------------------------------------------------


@router.get("/review-templates")
async def list_review_templates(
    review_type: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """获取评审模板列表。"""
    templates = await repo.get_review_templates(session, review_type)
    return {
        "templates": [
            {
                "id": t.id,
                "review_type": t.review_type,
                "name": t.name,
                "criteria": t.criteria,
                "rubric": t.rubric,
                "is_default": t.is_default,
            }
            for t in templates
        ]
    }


@router.post("/review-templates")
async def create_review_template(
    req: dict,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """创建评审模板（非默认）。"""
    template = await repo.create_review_template(
        session,
        review_type=req["review_type"],
        name=req["name"],
        criteria=req.get("criteria", []),
        rubric=req.get("rubric", []),
        is_default=False,
    )
    return {
        "ok": True,
        "template": {
            "id": template.id,
            "review_type": template.review_type,
            "name": template.name,
            "criteria": template.criteria,
            "rubric": template.rubric,
        },
    }


@router.put("/review-templates/{template_id}")
async def update_review_template(
    template_id: str,
    req: dict,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """更新评审模板（非默认模板可编辑）。"""
    template = await repo.update_review_template(
        session,
        template_id,
        name=req.get("name"),
        criteria=req.get("criteria"),
        rubric=req.get("rubric"),
    )
    if not template:
        raise HTTPException(400, "模板不存在或为默认模板不可编辑")
    return {
        "ok": True,
        "template": {
            "id": template.id,
            "review_type": template.review_type,
            "name": template.name,
            "criteria": template.criteria,
            "rubric": template.rubric,
        },
    }


@router.delete("/review-templates/{template_id}")
async def delete_review_template(
    template_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """删除评审模板（默认模板不可删除）。"""
    ok = await repo.delete_review_template(session, template_id)
    if not ok:
        raise HTTPException(400, "模板不存在或为默认模板不可删除")
    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """控制台实时事件通道。"""
    await hub.connect(websocket)
    try:
        # 启动时订阅所有事件
        async def on_event(event_type: str):
            async def cb(data):
                try:
                    await websocket.send_json({"event": event_type, "data": data})
                except Exception:
                    pass

            return cb

        # 把 WebSocket 当成订阅者
        for evt in [
            Events.PARENT_STATUS,
            Events.PARENT_CREATED,
            Events.PARENT_SCHEDULED,
            Events.PARENT_CONFIRMED,
            Events.WORKFLOW_CREATED,
            Events.WORKFLOW_STATUS,
            Events.WORKFLOW_PROGRESS,
            Events.WORKFLOW_LOG,
            Events.STAGE_STATUS,
            Events.STAGE_REVIEW_NEEDED,
            Events.CHAT_MESSAGE,
            Events.SYSTEM,
        ]:
            event_bus.subscribe(evt, await on_event(evt))

        # 阻塞保持连接
        while True:
            msg = await websocket.receive_text()
            if msg.strip() == "ping":
                await websocket.send_json({"event": "pong", "data": {}})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(websocket)


# ---------------------------------------------------------------------------
# 健康检查 / 元信息
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> dict:
    from app.agents.pool import expert_pool
    from app.infra.queues import scheduler_queue, stage_queue, workflow_queue

    return {
        "ok": True,
        "queues": {
            "scheduler": scheduler_queue.size,
            "workflow": workflow_queue.size,
            "stage": stage_queue.size,
        },
        "experts": expert_pool.types(),
    }


# ---------------------------------------------------------------------------
# 运行时配置（LLM 等）
# ---------------------------------------------------------------------------


@router.get("/settings")
async def get_settings() -> dict:
    """返回当前运行时配置（API key 脱敏）。"""
    from app import runtime_settings

    cfg = await runtime_settings.get_all_public()
    # 同时返回 key 清单，方便前端动态生成表单
    return {
        "settings": cfg,
        "keys": runtime_settings.SETTING_KEYS,
        "secret_keys": list(runtime_settings.SECRET_KEYS),
    }


@router.put("/settings")
async def put_settings(payload: dict, session: AsyncSession = Depends(get_session)) -> dict:
    """批量更新运行时配置。空字符串 = 清除（回退到默认）。"""
    from app import runtime_settings

    items = payload.get("settings", {}) if isinstance(payload, dict) else {}
    await runtime_settings.set_many(items)
    # 立即读回（看持久化结果）
    new_cfg = await runtime_settings.get_all_public()
    return {"ok": True, "settings": new_cfg}


@router.post("/settings/test")
async def test_settings(payload: dict) -> dict:
    """用当前/指定配置试调一次 LLM，验证连通性。"""
    from app import runtime_settings
    from app.pm import planner

    # 如果传了 override，就用 override，否则用已存配置
    override = payload.get("override", {}) if isinstance(payload, dict) else {}
    if override:
        await runtime_settings.set_many(override)

    try:
        # 用一个轻量任务探一下
        result = await planner._try_llm_plan(
            "ping", "respond with a single word: pong"
        )
        if result is None:
            return {"ok": False, "error": "LLM 未返回结果（可能未配置或调用失败）"}
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# 文档版本化管理
# ---------------------------------------------------------------------------

from app.db.schemas import (
    ChangeLogRead,
    DiffResult,
    DocumentCreate,
    DocumentRead,
    DocumentUpdate,
    DocumentVersionRead,
    ReviewCreate,
    ReviewRecordRead,
)
from app.services.diff import compute_diff


@router.post("/documents/", response_model=DocumentVersionRead)
async def create_document(
    req: DocumentCreate,
    session: AsyncSession = Depends(get_session),
) -> DocumentVersionRead:
    """创建文档（含初始版本）。"""
    v = await repo.create_document(
        session,
        parent_id=req.parent_id,
        stage_id=req.stage_id,
        doc_type=req.doc_type,
        title=req.title,
        content=req.content,
        author=req.author,
    )
    await session.commit()
    return DocumentVersionRead.model_validate(v)


@router.get("/documents/{doc_id}/", response_model=DocumentRead)
async def get_document(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
) -> DocumentRead:
    doc = await repo.get_document(session, doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    return DocumentRead.model_validate(doc)


@router.get("/tasks/{parent_id}/documents/", response_model=list[DocumentRead])
async def get_task_documents(
    parent_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[DocumentRead]:
    docs = await repo.get_documents_by_parent(session, parent_id)
    return [DocumentRead.model_validate(d) for d in docs]


@router.get("/stages/{stage_id}/document/", response_model=DocumentRead | None)
async def get_stage_document(
    stage_id: str,
    session: AsyncSession = Depends(get_session),
) -> DocumentRead | None:
    doc = await repo.get_document_by_stage(session, stage_id)
    if not doc:
        return None
    return DocumentRead.model_validate(doc)


@router.put("/documents/{doc_id}/", response_model=DocumentVersionRead)
async def save_document_version(
    doc_id: str,
    req: DocumentUpdate,
    session: AsyncSession = Depends(get_session),
) -> DocumentVersionRead:
    """保存新版本（不覆盖历史版本）。"""
    doc = await repo.get_document(session, doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    v = await repo.save_new_version(
        session,
        doc_id=doc_id,
        content=req.content,
        author=req.author,
        change_summary=req.change_summary,
    )
    await session.commit()
    return DocumentVersionRead.model_validate(v)


@router.get("/documents/{doc_id}/versions/", response_model=list[DocumentVersionRead])
async def list_versions(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[DocumentVersionRead]:
    versions = await repo.get_document_versions(session, doc_id)
    return [DocumentVersionRead.model_validate(v) for v in versions]


@router.get("/documents/{doc_id}/versions/{version}/", response_model=DocumentVersionRead)
async def get_version(
    doc_id: str,
    version: int,
    session: AsyncSession = Depends(get_session),
) -> DocumentVersionRead:
    v = await repo.get_document_version(session, doc_id, version)
    if not v:
        raise HTTPException(404, f"版本 {version} 不存在")
    return DocumentVersionRead.model_validate(v)


@router.get("/documents/{doc_id}/diff/", response_model=DiffResult)
async def diff_versions(
    doc_id: str,
    v1: int = Query(..., description="起始版本号"),
    v2: int = Query(..., description="终止版本号"),
    session: AsyncSession = Depends(get_session),
) -> DiffResult:
    ver1 = await repo.get_document_version(session, doc_id, v1)
    ver2 = await repo.get_document_version(session, doc_id, v2)
    if not ver1 or not ver2:
        raise HTTPException(404, "版本不存在")
    result = compute_diff(ver1.content, ver2.content)
    result.v1 = v1
    result.v2 = v2
    return result


@router.post("/documents/{doc_id}/reviews/", response_model=ReviewRecordRead)
async def create_review(
    doc_id: str,
    req: ReviewCreate,
    session: AsyncSession = Depends(get_session),
) -> ReviewRecordRead:
    doc = await repo.get_document(session, doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    rec = await repo.create_review_record(
        session,
        doc_id=doc_id,
        stage_id=req.stage_id,
        version=req.version,
        reviewer=req.reviewer,
        decision=req.decision,
        comments=req.comments,
        scores=req.scores,
    )
    await session.commit()
    return ReviewRecordRead.model_validate(rec)


@router.get("/documents/{doc_id}/reviews/", response_model=list[ReviewRecordRead])
async def list_reviews(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[ReviewRecordRead]:
    records = await repo.get_review_records(session, doc_id)
    return [ReviewRecordRead.model_validate(r) for r in records]


@router.get("/change-log/", response_model=list[ChangeLogRead])
async def get_change_log(
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> list[ChangeLogRead]:
    records = await repo.get_change_log(session, entity_type, entity_id, limit)
    return [ChangeLogRead.model_validate(r) for r in records]
