"""专家 Agent 基类。"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field

from app.db.models import StageInstance, WorkflowTask
from app.logging import logger


# ---------------------------------------------------------------------------
# 输入输出
# ---------------------------------------------------------------------------


@dataclass
class StageContext:
    """流程引擎向专家 Agent 传递的上下文。"""

    workflow: WorkflowTask
    stage: StageInstance
    parent_description: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    # 用于发送心跳的回调
    on_heartbeat: Optional[Callable[[], Awaitable[None]]] = None

    def short(self) -> str:
        return f"[{self.workflow.title} / {self.stage.name.value}]"


class StageResult(BaseModel):
    """专家 Agent 的产物。"""

    summary: str = Field(..., description="一句话总结产出")
    artifacts: dict[str, Any] = Field(default_factory=dict, description="结构化产物")
    success: bool = True
    # 评审类阶段使用
    approved: Optional[bool] = None
    review_comment: Optional[str] = None


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------


class BaseExpertAgent(ABC):
    """所有专家 Agent 的基类。

    - `expert_type` 唯一标识专家种类（reviewer、developer 等）。
    - `run` 是协程，**必须**是非阻塞的（短时运行），保持单进程事件循环通畅。
    - 子类实现 `_execute` 返回 StageResult。
    """

    expert_type: str = "base"

    def __init__(self, expert_type: str | None = None) -> None:
        if expert_type:
            self.expert_type = expert_type

    # 子类必须实现
    @abstractmethod
    async def _execute(self, ctx: StageContext) -> StageResult: ...

    # 子类可重写
    def system_prompt(self) -> str:
        return f"You are a {self.expert_type}."

    async def run(self, ctx: StageContext) -> StageResult:
        """执行阶段并把心跳周期地更新到 DB。"""
        logger.info("专家 {} 开始: {}", self.expert_type, ctx.short())
        if ctx.on_heartbeat:
            await ctx.on_heartbeat()
        try:
            result = await self._execute(ctx)
            logger.info(
                "专家 {} 完成: {} -> success={}",
                self.expert_type,
                ctx.short(),
                result.success,
            )
            return result
        except Exception as e:
            logger.exception("专家 {} 失败: {}", self.expert_type, ctx.short())
            return StageResult(
                summary=f"执行失败: {e}",
                artifacts={},
                success=False,
                review_comment=str(e),
            )
