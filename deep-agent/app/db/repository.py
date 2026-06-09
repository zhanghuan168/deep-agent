"""数据访问层（Repository）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    ChangeLog,
    ConversationHistory,
    ConversationRole,
    Document,
    DocumentVersion,
    ParentTask,
    ParentTaskStatus,
    ReviewRecord,
    ReviewType,
    ReviewTemplate,
    StageInstance,
    StageName,
    StageStatus,
    TaskLog,
    TaskLogLevel,
    WorkflowTask,
    WorkflowTaskStatus,
)


# ---------------------------------------------------------------------------
# 父任务
# ---------------------------------------------------------------------------


async def create_parent_task(
    session: AsyncSession,
    title: str,
    description: str = "",
    plan: Optional[dict] = None,
    status: ParentTaskStatus = ParentTaskStatus.DRAFT,
) -> ParentTask:
    task = ParentTask(
        title=title,
        description=description,
        plan=plan,
        status=status,
    )
    session.add(task)
    await session.flush()
    return task


async def get_parent_task(session: AsyncSession, parent_id: str) -> Optional[ParentTask]:
    stmt = (
        select(ParentTask)
        .where(ParentTask.id == parent_id)
        .options(selectinload(ParentTask.workflow_tasks).selectinload(WorkflowTask.stages))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_parent_tasks(session: AsyncSession) -> Sequence[ParentTask]:
    stmt = (
        select(ParentTask)
        .order_by(ParentTask.created_at.desc())
        .options(
            selectinload(ParentTask.workflow_tasks).selectinload(WorkflowTask.stages)
        )
    )
    return (await session.execute(stmt)).scalars().all()


async def update_parent_status(
    session: AsyncSession, parent_id: str, status: ParentTaskStatus, plan: Optional[dict] = None
) -> None:
    values: dict = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if plan is not None:
        values["plan"] = plan
    await session.execute(
        update(ParentTask).where(ParentTask.id == parent_id).values(**values)
    )


# ---------------------------------------------------------------------------
# 工作项
# ---------------------------------------------------------------------------


async def create_workflow_task(
    session: AsyncSession,
    parent_id: str,
    title: str,
    description: str = "",
    template: Optional[list[StageName]] = None,
    priority: int = 5,
    inputs: Optional[dict] = None,
    model_config: Optional[str] = None,
) -> WorkflowTask:
    """创建工作项及其阶段实例。

    新7阶段：
    1. REQUIREMENT_ANALYSIS  - 需求分析
    2. REQUIREMENT_REVIEW    - 需求评审（独立专家）
    3. TECHNICAL_DESIGN       - 技术方案设计
    4. TECHNICAL_REVIEW       - 技术方案评审（独立专家）
    5. TASK_BREAKDOWN         - 任务拆解
    6. IMPLEMENTATION         - 编码实现（TDD）
    7. CODE_REVIEW            - 代码审查（独立专家）
    8. TESTING                - 功能与集成测试
    """
    template = template or [
        StageName.REQUIREMENT_ANALYSIS,
        StageName.REQUIREMENT_REVIEW,
        StageName.TECHNICAL_DESIGN,
        StageName.TECHNICAL_REVIEW,
        StageName.TASK_BREAKDOWN,
        StageName.IMPLEMENTATION,
        StageName.CODE_REVIEW,
        StageName.TESTING,
    ]
    task = WorkflowTask(
        parent_id=parent_id,
        title=title,
        description=description,
        priority=priority,
        inputs=inputs,
        model_config=model_config,
        template=[s.value for s in template],
    )
    session.add(task)
    await session.flush()
    # 同步创建阶段实例
    for idx, stage in enumerate(template):
        stage_info = _stage_info_for_stage(stage)
        session.add(
            StageInstance(
                workflow_id=task.id,
                name=stage,
                order_index=idx,
                status=StageStatus.PENDING,
                expert_type=stage_info["expert"],
                model_config=stage_info.get("model"),
            )
        )
    await session.flush()
    return task


def _stage_info_for_stage(stage: StageName) -> dict:
    """返回阶段配置：expert类型、是否评审阶段。"""
    mapping = {
        StageName.REQUIREMENT_ANALYSIS: {"expert": "requirement_analyst", "review_type": None},
        StageName.REQUIREMENT_REVIEW:   {"expert": "cross_reviewer",   "review_type": ReviewType.REQUIREMENT_REVIEW.value},
        StageName.TECHNICAL_DESIGN:     {"expert": "designer",          "review_type": None},
        StageName.TECHNICAL_REVIEW:    {"expert": "cross_reviewer",   "review_type": ReviewType.TECHNICAL_REVIEW.value},
        StageName.TASK_BREAKDOWN:       {"expert": "requirement_analyst", "review_type": None},
        StageName.IMPLEMENTATION:       {"expert": "developer",         "review_type": None},
        StageName.CODE_REVIEW:          {"expert": "cross_reviewer",   "review_type": ReviewType.CODE_REVIEW.value},
        StageName.TESTING:              {"expert": "tester",            "review_type": None},
    }
    return mapping.get(stage, {"expert": "developer", "review_type": None})


async def get_workflow_task(
    session: AsyncSession, workflow_id: str
) -> Optional[WorkflowTask]:
    stmt = (
        select(WorkflowTask)
        .where(WorkflowTask.id == workflow_id)
        .options(selectinload(WorkflowTask.stages))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def update_workflow_status(
    session: AsyncSession,
    workflow_id: str,
    *,
    status: Optional[WorkflowTaskStatus] = None,
    progress: Optional[int] = None,
    heartbeat_at: Optional[datetime] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    last_error: Optional[str] = None,
    outputs: Optional[dict] = None,
    retries: Optional[int] = None,
) -> None:
    values: dict = {}
    if status is not None:
        values["status"] = status
    if progress is not None:
        values["progress"] = progress
    if heartbeat_at is not None:
        values["heartbeat_at"] = heartbeat_at
    if started_at is not None:
        values["started_at"] = started_at
    if finished_at is not None:
        values["finished_at"] = finished_at
    if last_error is not None:
        values["last_error"] = last_error
    if outputs is not None:
        values["outputs"] = outputs
    if retries is not None:
        values["retries"] = retries
    if not values:
        return
    await session.execute(
        update(WorkflowTask).where(WorkflowTask.id == workflow_id).values(**values)
    )


# ---------------------------------------------------------------------------
# 阶段
# ---------------------------------------------------------------------------


async def update_stage(
    session: AsyncSession,
    stage_id: str,
    *,
    status: Optional[StageStatus] = None,
    output: Optional[dict] = None,
    review_comment: Optional[str] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    requirement_items: Optional[list] = None,
) -> None:
    values: dict = {}
    if status is not None:
        values["status"] = status
    if output is not None:
        values["output"] = output
    if review_comment is not None:
        values["review_comment"] = review_comment
    if started_at is not None:
        values["started_at"] = started_at
    if finished_at is not None:
        values["finished_at"] = finished_at
    if requirement_items is not None:
        values["requirement_items"] = requirement_items
    if values:
        await session.execute(
            update(StageInstance).where(StageInstance.id == stage_id).values(**values)
        )


async def get_next_pending_stage(
    session: AsyncSession, workflow_id: str
) -> Optional[StageInstance]:
    stmt = (
        select(StageInstance)
        .where(StageInstance.workflow_id == workflow_id)
        .where(StageInstance.status.in_([StageStatus.PENDING, StageStatus.FAILED]))
        .order_by(StageInstance.order_index.asc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


async def add_log(
    session: AsyncSession,
    message: str,
    *,
    level: TaskLogLevel = TaskLogLevel.INFO,
    parent_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    data: Optional[dict] = None,
) -> TaskLog:
    log = TaskLog(
        parent_id=parent_id,
        workflow_id=workflow_id,
        level=level,
        message=message,
        data=data,
    )
    session.add(log)
    await session.flush()
    return log


async def list_logs(
    session: AsyncSession,
    *,
    parent_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    limit: int = 200,
) -> Sequence[TaskLog]:
    stmt = select(TaskLog).order_by(TaskLog.created_at.desc()).limit(limit)
    if parent_id:
        stmt = stmt.where(TaskLog.parent_id == parent_id)
    if workflow_id:
        stmt = stmt.where(TaskLog.workflow_id == workflow_id)
    return (await session.execute(stmt)).scalars().all()


# ---------------------------------------------------------------------------
# 对话历史
# ---------------------------------------------------------------------------


async def add_conversation(
    session: AsyncSession,
    role: ConversationRole,
    content: str,
    *,
    parent_id: Optional[str] = None,
    data: Optional[dict] = None,
) -> ConversationHistory:
    item = ConversationHistory(
        role=role, content=content, parent_id=parent_id, data=data
    )
    session.add(item)
    await session.flush()
    return item


async def list_conversations(
    session: AsyncSession, parent_id: str, limit: int = 100
) -> Sequence[ConversationHistory]:
    stmt = (
        select(ConversationHistory)
        .where(ConversationHistory.parent_id == parent_id)
        .order_by(ConversationHistory.created_at.asc())
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


# ---------------------------------------------------------------------------
# 应用配置（key-value）
# ---------------------------------------------------------------------------


async def get_setting(session: AsyncSession, key: str) -> Optional[str]:
    """读取单个配置项。"""
    from app.db.models import AppSetting

    row = await session.get(AppSetting, key)
    return row.value if row else None


async def set_setting(session: AsyncSession, key: str, value: Optional[str]) -> None:
    """写入单个配置项（存在则更新）。"""
    from app.db.models import AppSetting

    row = await session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=value)
        session.add(row)
    else:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)


async def get_all_settings(session: AsyncSession) -> dict[str, str]:
    """获取所有配置（用于前端 /api/settings）。"""
    from app.db.models import AppSetting

    stmt = select(AppSetting)
    rows = (await session.execute(stmt)).scalars().all()
    return {r.key: (r.value or "") for r in rows}


async def apply_settings_bulk(
    session: AsyncSession, items: dict[str, Optional[str]]
) -> None:
    """批量写入（PUT /api/settings 用）。"""
    from app.db.models import AppSetting

    if not items:
        return
    keys = list(items.keys())
    stmt = select(AppSetting).where(AppSetting.key.in_(keys))
    existing = {r.key: r for r in (await session.execute(stmt)).scalars().all()}
    now = datetime.now(timezone.utc)
    for k, v in items.items():
        if k in existing:
            existing[k].value = v
            existing[k].updated_at = now
        else:
            session.add(AppSetting(key=k, value=v, updated_at=now))


# ---------------------------------------------------------------------------
# 需求清单（requirement_analysis 阶段输出）
# ---------------------------------------------------------------------------


async def update_stage_requirement_items(
    session: AsyncSession,
    stage_id: str,
    items: list[dict],
    confirmed: bool = False,
) -> None:
    """更新需求清单内容及确认状态。"""
    stage = await session.get(StageInstance, stage_id)
    if not stage:
        return
    stage.requirement_items = items
    stage.requirement_confirmed = confirmed
    stage.updated_at = datetime.now(timezone.utc)


async def get_stage_requirement_items(
    session: AsyncSession, stage_id: str
) -> tuple[Optional[list], bool]:
    """获取需求清单和确认状态。"""
    stage = await session.get(StageInstance, stage_id)
    if not stage:
        return None, False
    return stage.requirement_items, stage.requirement_confirmed


# ---------------------------------------------------------------------------
# 评审模板
# ---------------------------------------------------------------------------


async def create_review_template(
    session: AsyncSession,
    review_type: str,
    name: str,
    criteria: list[dict],
    rubric: Optional[list[str]] = None,
    is_default: bool = False,
) -> ReviewTemplate:
    """创建评审模板。"""
    template = ReviewTemplate(
        review_type=review_type,
        name=name,
        criteria=criteria,
        rubric=rubric or [],
        is_default=is_default,
    )
    session.add(template)
    await session.flush()
    return template


async def get_review_templates(
    session: AsyncSession, review_type: Optional[str] = None
) -> Sequence[ReviewTemplate]:
    """获取评审模板列表。"""
    if review_type:
        stmt = select(ReviewTemplate).where(ReviewTemplate.review_type == review_type)
    else:
        stmt = select(ReviewTemplate)
    return (await session.execute(stmt)).scalars().all()


async def get_default_review_template(
    session: AsyncSession, review_type: str
) -> Optional[ReviewTemplate]:
    """获取指定类型的默认评审模板。"""
    stmt = select(ReviewTemplate).where(
        ReviewTemplate.review_type == review_type,
        ReviewTemplate.is_default == True,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def update_review_template(
    session: AsyncSession,
    template_id: str,
    *,
    name: Optional[str] = None,
    criteria: Optional[list] = None,
    rubric: Optional[list] = None,
) -> Optional[ReviewTemplate]:
    """更新评审模板（非默认模板）。"""
    template = await session.get(ReviewTemplate, template_id)
    if not template or template.is_default:
        return None
    if name is not None:
        template.name = name
    if criteria is not None:
        template.criteria = criteria
    if rubric is not None:
        template.rubric = rubric
    template.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return template


async def delete_review_template(session: AsyncSession, template_id: str) -> bool:
    """删除评审模板（不能删除默认模板）。"""
    template = await session.get(ReviewTemplate, template_id)
    if not template or template.is_default:
        return False
    await session.delete(template)
    return True


async def seed_default_review_templates(session: AsyncSession) -> None:
    """初始化默认评审模板（如果不存在）。"""
    existing = await session.execute(select(ReviewTemplate).where(ReviewTemplate.is_default == True))
    if existing.scalars().first():
        return  # 已有默认模板

    defaults = [
        ReviewTemplate(
            review_type=ReviewType.REQUIREMENT_REVIEW.value,
            name="需求评审标准模板",
            criteria=[
                {"point": "需求完整性", "description": "是否覆盖了用户提出的所有功能点"},
                {"point": "需求明确性", "description": "每条需求描述是否清晰、无歧义"},
                {"point": "需求可测试性", "description": "每条需求是否可以转化为可执行的测试用例"},
                {"point": "优先级合理性", "description": "需求优先级是否符合业务价值排序"},
                {"point": "依赖关系", "description": "需求之间的依赖是否已标注"},
            ],
            rubric=["完整性", "明确性", "可测试性", "优先级", "依赖管理"],
            is_default=True,
        ),
        ReviewTemplate(
            review_type=ReviewType.TECHNICAL_REVIEW.value,
            name="技术方案评审标准模板",
            criteria=[
                {"point": "架构合理性", "description": "整体架构是否清晰、解耦、扩展性好"},
                {"point": "接口设计", "description": "API 接口是否符合 RESTful 规范、版本管理是否合理"},
                {"point": "数据模型", "description": "数据库 schema 设计是否合理、索引是否恰当"},
                {"point": "安全性", "description": "是否有 SQL 注入、XSS、权限控制等安全考虑"},
                {"point": "性能", "description": "是否有性能风险、是否需要缓存/异步处理"},
                {"point": "技术选型", "description": "技术栈选择是否合适、是否有更优方案"},
            ],
            rubric=["架构", "接口", "数据模型", "安全", "性能", "技术选型"],
            is_default=True,
        ),
        ReviewTemplate(
            review_type=ReviewType.CODE_REVIEW.value,
            name="代码审查标准模板",
            criteria=[
                {"point": "代码正确性", "description": "逻辑是否正确、边界条件是否处理"},
                {"point": "代码可读性", "description": "命名是否清晰、注释是否充分、函数长度是否合理"},
                {"point": "代码风格", "description": "是否符合 PEP8、团队代码规范"},
                {"point": "测试覆盖", "description": "是否有单元测试、覆盖率是否达标"},
                {"point": "错误处理", "description": "是否有完善的异常处理、日志记录"},
                {"point": "安全与性能", "description": "是否有明显的安全漏洞或性能问题"},
            ],
            rubric=["正确性", "可读性", "风格", "测试", "健壮性", "安全性能"],
            is_default=True,
        ),
    ]
    for t in defaults:
        session.add(t)
    await session.flush()


async def confirm_stage_requirement(session: AsyncSession, stage_id: str) -> bool:
    """用户确认需求清单后调用此方法，将阶段状态从 NEEDS_REVIEW 改为 SUCCEEDED，推进工作流。"""
    stage = await session.get(StageInstance, stage_id)
    if not stage:
        return False
    if stage.status != StageStatus.NEEDS_REVIEW:
        return False
    stage.status = StageStatus.SUCCEEDED
    stage.requirement_confirmed = True
    stage.finished_at = datetime.now(timezone.utc)
    stage.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# 文档主表
# ---------------------------------------------------------------------------


async def create_document(
    session: AsyncSession,
    parent_id: str,
    stage_id: Optional[str],
    doc_type: str,
    title: str,
    content: str,
    author: str,
) -> DocumentVersion:
    """创建文档及第一个版本。"""
    doc = Document(
        parent_id=parent_id,
        stage_id=stage_id,
        doc_type=doc_type,
        title=title,
        current_version=1,
    )
    session.add(doc)
    await session.flush()

    version = DocumentVersion(
        document_id=doc.id,
        version=1,
        content=content,
        author=author,
        change_summary="initial",
    )
    session.add(version)

    if stage_id:
        stage = await session.get(StageInstance, stage_id)
        if stage:
            stage.current_document_id = doc.id

    await log_change(session, "document", doc.id, "create", author, {"version": 1})
    await session.flush()
    return version


async def get_document(session: AsyncSession, doc_id: str) -> Optional[Document]:
    """获取文档（含版本和评审记录）。"""
    stmt = (
        select(Document)
        .where(Document.id == doc_id)
        .options(
            selectinload(Document.versions),
            selectinload(Document.reviews),
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_documents_by_parent(
    session: AsyncSession, parent_id: str
) -> Sequence[Document]:
    """获取父任务下的所有文档。"""
    stmt = select(Document).where(Document.parent_id == parent_id)
    return (await session.execute(stmt)).scalars().all()


async def get_document_by_stage(
    session: AsyncSession, stage_id: str
) -> Optional[Document]:
    """通过阶段 ID 查找当前文档。"""
    stmt = select(Document).where(Document.stage_id == stage_id)
    return (await session.execute(stmt)).scalar_one_or_none()


# ---------------------------------------------------------------------------
# 版本操作
# ---------------------------------------------------------------------------


async def save_new_version(
    session: AsyncSession,
    doc_id: str,
    content: str,
    author: str,
    change_summary: str = "",
) -> DocumentVersion:
    """保存文档新版本。"""
    doc = await get_document(session, doc_id)
    if not doc:
        raise ValueError(f"Document {doc_id} not found")

    new_ver = doc.current_version + 1
    version = DocumentVersion(
        document_id=doc_id,
        version=new_ver,
        content=content,
        author=author,
        change_summary=change_summary,
    )
    session.add(version)

    doc.current_version = new_ver
    doc.updated_at = datetime.now(timezone.utc)

    await log_change(session, "document", doc_id, "version_bump", author, {"version": new_ver})
    await session.flush()
    return version


async def get_document_versions(
    session: AsyncSession, doc_id: str
) -> Sequence[DocumentVersion]:
    """获取文档所有版本（升序）。"""
    stmt = (
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc_id)
        .order_by(DocumentVersion.version.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def get_document_version(
    session: AsyncSession, doc_id: str, version: int
) -> Optional[DocumentVersion]:
    """获取指定版本。"""
    stmt = select(DocumentVersion).where(
        DocumentVersion.document_id == doc_id,
        DocumentVersion.version == version,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ---------------------------------------------------------------------------
# 评审记录
# ---------------------------------------------------------------------------


async def create_review_record(
    session: AsyncSession,
    doc_id: str,
    stage_id: Optional[str],
    version: int,
    reviewer: str,
    decision: str,
    comments: str = "",
    scores: Optional[dict] = None,
    attachment_refs: Optional[list] = None,
) -> ReviewRecord:
    """创建评审记录。"""
    record = ReviewRecord(
        document_id=doc_id,
        stage_id=stage_id,
        version=version,
        reviewer=reviewer,
        decision=decision,
        comments=comments,
        scores=scores,
        attachment_refs=attachment_refs,
    )
    session.add(record)
    await log_change(session, "review", doc_id, "review", reviewer, {"version": version, "decision": decision})
    await session.flush()
    return record


async def get_review_records(
    session: AsyncSession, doc_id: str
) -> Sequence[ReviewRecord]:
    """获取文档所有评审记录。"""
    stmt = select(ReviewRecord).where(ReviewRecord.document_id == doc_id)
    return (await session.execute(stmt)).scalars().all()


# ---------------------------------------------------------------------------
# 变更日志
# ---------------------------------------------------------------------------


async def log_change(
    session: AsyncSession,
    entity_type: str,
    entity_id: str,
    action: str,
    actor: str,
    detail: Optional[dict] = None,
) -> ChangeLog:
    """写入变更日志。"""
    entry = ChangeLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor=actor,
        detail=detail,
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_change_log(
    session: AsyncSession,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = 50,
) -> Sequence[ChangeLog]:
    """查询变更日志。"""
    stmt = select(ChangeLog).order_by(ChangeLog.created_at.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(ChangeLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(ChangeLog.entity_id == entity_id)
    return (await session.execute(stmt)).scalars().all()
