"""ORM 模型定义。"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """所有模型的基类。"""


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class ParentTaskStatus(str, enum.Enum):
    """父任务状态。"""

    DRAFT = "draft"  # 老板刚提需求
    CONFIRMED = "confirmed"  # 项目经理已确认，准备规划
    SCHEDULED = "scheduled"  # 已拆解为子工作项
    IN_PROGRESS = "in_progress"  # 至少一个子工作项在跑
    BLOCKED = "blocked"  # 等待人工干预
    COMPLETED = "completed"  # 全部完成
    FAILED = "failed"  # 失败


class WorkflowTaskStatus(str, enum.Enum):
    """子工作项状态。"""

    CREATED = "created"
    IN_PROGRESS = "in_progress"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageName(str, enum.Enum):
    """新7阶段研发流程。"""

    REQUIREMENT_ANALYSIS = "requirement_analysis"  # 需求分析 → 输出清单
    REQUIREMENT_REVIEW = "requirement_review"       # 需求评审 → 独立专家交叉检视
    TECHNICAL_DESIGN = "technical_design"           # 技术方案设计
    TECHNICAL_REVIEW = "technical_review"          # 技术方案评审 → 独立专家交叉检视
    TASK_BREAKDOWN = "task_breakdown"              # 任务拆解
    IMPLEMENTATION = "implementation"              # 编码实现（TDD）
    CODE_REVIEW = "code_review"                   # 代码审查 → 独立专家交叉检视
    TESTING = "testing"                           # 功能与集成测试


class StageStatus(str, enum.Enum):
    """阶段实例状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs_review"


class TaskLogLevel(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    DEBUG = "debug"


class ConversationRole(str, enum.Enum):
    BOSS = "boss"
    PROJECT_MANAGER = "project_manager"
    SYSTEM = "system"


# ---------------------------------------------------------------------------
# 表
# ---------------------------------------------------------------------------


class ParentTask(Base):
    """父任务（老板下达的顶层目标）。"""

    __tablename__ = "parent_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ParentTaskStatus] = mapped_column(
        Enum(ParentTaskStatus), default=ParentTaskStatus.DRAFT, index=True
    )
    plan: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # 项目经理的计划
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )

    workflow_tasks: Mapped[list["WorkflowTask"]] = relationship(
        back_populates="parent_task", cascade="all, delete-orphan"
    )


class WorkflowTask(Base):
    """子工作项（不可再分的执行单元）。"""

    __tablename__ = "workflow_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    parent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("parent_tasks.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[WorkflowTaskStatus] = mapped_column(
        Enum(WorkflowTaskStatus),
        default=WorkflowTaskStatus.CREATED,
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=5)
    # 进度 0-100
    progress: Mapped[int] = mapped_column(Integer, default=0)
    # 输入输出物
    inputs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    outputs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # 心跳与重试
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 该工作项使用的模型（覆盖全局配置）
    model_config: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # 阶段模板
    template: Mapped[list] = mapped_column(JSON, default=list)
    # 时间
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    parent_task: Mapped[ParentTask] = relationship(back_populates="workflow_tasks")
    stages: Mapped[list["StageInstance"]] = relationship(
        back_populates="workflow_task",
        cascade="all, delete-orphan",
        order_by="StageInstance.order_index",
    )


class StageInstance(Base):
    """阶段实例。"""

    __tablename__ = "stage_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_tasks.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[StageName] = mapped_column(Enum(StageName))
    order_index: Mapped[int] = mapped_column(Integer)
    status: Mapped[StageStatus] = mapped_column(
        Enum(StageStatus), default=StageStatus.PENDING, index=True
    )
    # 该阶段被指派的专家类型
    expert_type: Mapped[str] = mapped_column(String(64))
    # 阶段产物 / 评审意见
    output: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    review_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 该阶段使用的模型（可覆盖全局配置）
    model_config: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # 评审模板ID（评审类阶段使用）
    review_template_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # 需求清单（requirement_analysis 阶段输出）
    requirement_items: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # 需求清单确认状态
    requirement_confirmed: Mapped[bool] = mapped_column(default=False)
    # 交叉评审者 ID（评审阶段，记录是哪个工作项的作者）
    cross_reviewer_of: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # 当前关联的文档 ID
    current_document_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # 时间
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )

    workflow_task: Mapped[WorkflowTask] = relationship(back_populates="stages")

    __table_args__ = (
        Index("ix_stage_workflow_order", "workflow_id", "order_index"),
    )


class ReviewTemplate(Base):
    """评审模板。可配置不同评审类型的检查点。"""

    __tablename__ = "review_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # 关联的评审类型
    review_type: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    # 评审检查点列表（JSON 格式）
    criteria: Mapped[list] = mapped_column(JSON, default=list)
    # 评审维度（用于评分）
    rubric: Mapped[list] = mapped_column(JSON, default=list)
    # 是否为系统默认模板（用户不可删除）
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )


class TaskLog(Base):
    """任务日志。"""

    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("workflow_tasks.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("parent_tasks.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[TaskLogLevel] = mapped_column(
        Enum(TaskLogLevel), default=TaskLogLevel.INFO
    )
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class ConversationHistory(Base):
    """老板与项目经理的对话历史。"""

    __tablename__ = "conversation_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("parent_tasks.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[ConversationRole] = mapped_column(Enum(ConversationRole))
    content: Mapped[str] = mapped_column(Text)
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class AppSetting(Base):
    """运行时配置（key-value 形式，可在 UI 里改）。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )


class ReviewType(str, enum.Enum):
    """评审类型。"""

    REQUIREMENT_REVIEW = "requirement_review"    # 需求评审
    TECHNICAL_REVIEW = "technical_review"       # 技术方案评审
    CODE_REVIEW = "code_review"                 # 代码审查


class Document(Base):
    """文档主表。"""
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    parent_id: Mapped[str] = mapped_column(String(36), ForeignKey("parent_tasks.id", ondelete="CASCADE"), index=True)
    stage_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("stage_instances.id", ondelete="SET NULL"), nullable=True, index=True)
    doc_type: Mapped[str] = mapped_column(String(32))  # requirement/technical/code_review/design/other
    title: Mapped[str] = mapped_column(String(255))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.version")
    reviews: Mapped[list["ReviewRecord"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentVersion(Base):
    """文档版本表（append-only）。"""
    __tablename__ = "document_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(128))
    change_summary: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    document: Mapped[Document] = relationship(back_populates="versions")

    __table_args__ = (Index("ix_doc_version", "document_id", "version", unique=True),)


class ReviewRecord(Base):
    """评审记录表。"""
    __tablename__ = "review_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    stage_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("stage_instances.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer)  # 评审针对的版本号
    reviewer: Mapped[str] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(16))  # approve/reject/comment
    scores: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    comments: Mapped[str] = mapped_column(Text, default="")
    attachment_refs: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    document: Mapped[Document] = relationship(back_populates="reviews")


class ChangeLog(Base):
    """变更日志表（append-only）。"""
    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(128))
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
