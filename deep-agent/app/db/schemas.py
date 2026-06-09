"""Pydantic DTO（API 层使用）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import (
    ConversationRole,
    ParentTaskStatus,
    StageName,
    StageStatus,
    WorkflowTaskStatus,
)


class ORMModel(BaseModel):
    """从 ORM 实例构造的基类。"""

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# 父任务
# ---------------------------------------------------------------------------


class ParentTaskCreate(BaseModel):
    title: str
    description: str = ""


class ParentTaskRead(ORMModel):
    id: str
    title: str
    description: str
    status: ParentTaskStatus
    plan: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime


class ParentTaskWithChildren(ParentTaskRead):
    workflow_tasks: list["WorkflowTaskRead"] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 工作项 / 阶段
# ---------------------------------------------------------------------------


class StageInstanceRead(ORMModel):
    id: str
    name: StageName
    order_index: int
    status: StageStatus
    expert_type: str
    output: Optional[dict[str, Any]] = None
    review_comment: Optional[str] = None
    requirement_items: Optional[list] = None
    requirement_confirmed: Optional[bool] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class WorkflowTaskRead(ORMModel):
    id: str
    parent_id: str
    title: str
    description: str
    status: WorkflowTaskStatus
    priority: int
    progress: int
    inputs: Optional[dict[str, Any]] = None
    outputs: Optional[dict[str, Any]] = None
    retries: int
    last_error: Optional[str] = None
    template: list[str] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    stages: list[StageInstanceRead] = Field(default_factory=list)


class TaskLogRead(ORMModel):
    id: int
    workflow_id: Optional[str]
    parent_id: Optional[str]
    level: str
    message: str
    data: Optional[dict[str, Any]] = None
    created_at: datetime


# ---------------------------------------------------------------------------
# 聊天
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """老板下达的聊天消息。"""

    message: str
    parent_id: Optional[str] = None  # 如果不传则创建新父任务


class ChatMessage(BaseModel):
    role: ConversationRole
    content: str
    parent_id: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None
    intent: Optional[str] = "plan"  # "chat" | "plan"


class ConfirmPlanRequest(BaseModel):
    parent_id: str
    plan: dict[str, Any]


class ReviewDecision(BaseModel):
    """人工对评审的决策。"""

    decision: str = Field(..., description="approve | reject")
    comment: str = ""


ParentTaskWithChildren.model_rebuild()


# ---------------------------------------------------------------------------
# 文档版本化
# ---------------------------------------------------------------------------


class DocumentVersionRead(ORMModel):
    id: str
    document_id: str
    version: int
    content: str
    author: str
    change_summary: str
    created_at: datetime


class DocumentRead(ORMModel):
    id: str
    parent_id: str
    stage_id: Optional[str]
    doc_type: str
    title: str
    current_version: int
    created_at: datetime
    updated_at: datetime
    versions: list[DocumentVersionRead] = Field(default_factory=list)


class ReviewRecordRead(ORMModel):
    id: str
    document_id: str
    stage_id: Optional[str]
    version: int
    reviewer: str
    decision: str
    scores: Optional[dict]
    comments: str
    attachment_refs: Optional[list]
    created_at: datetime


class ChangeLogRead(ORMModel):
    id: int
    entity_type: str
    entity_id: str
    action: str
    actor: str
    detail: Optional[dict]
    created_at: datetime


class DocumentCreate(BaseModel):
    parent_id: str
    stage_id: Optional[str] = None
    doc_type: str
    title: str
    content: str
    author: str


class DocumentUpdate(BaseModel):
    content: str
    author: str
    change_summary: str = ""


class ReviewCreate(BaseModel):
    stage_id: Optional[str] = None
    version: int
    reviewer: str
    decision: str  # approve/reject/comment
    comments: str = ""
    scores: Optional[dict] = None
    attachment_refs: Optional[list] = None


class DiffResult(BaseModel):
    v1: int
    v2: int
    additions: list[str]
    deletions: list[str]
    unchanged: list[str]
