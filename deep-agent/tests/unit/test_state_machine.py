#!/usr/bin/env python3
"""
deep-agent 任务管理状态机测试
测试 ParentTask / WorkflowTask / Stage 的状态流转逻辑

运行: cd /root/.openclaw/workspace/deep-agent && pytest tests/unit/test_state_machine.py -v
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call
from contextlib import asynccontextmanager

# StageAction enum from workflow_engine (used in assertions)
from app.engine.workflow_engine import StageAction

# ------------------------------------------------------------------
# 枚举值（从 models.py 复制）
# ------------------------------------------------------------------

class ParentTaskStatus:
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowTaskStatus:
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageName:
    """测试用 StageName 枚举。直接返回字符串，但提供 .value 访问。"""
    REQUIREMENT_ANALYSIS = "requirement_analysis"
    REQUIREMENT_REVIEW = "requirement_review"
    TECHNICAL_DESIGN = "technical_design"
    TECHNICAL_REVIEW = "technical_review"
    TASK_BREAKDOWN = "task_breakdown"
    IMPLEMENTATION = "implementation"
    CODE_REVIEW = "code_review"
    TESTING = "testing"

    @property
    def value(self):
        return self


class _MockStageName:
    """mock StageName 成员，提供 .value 访问。"""
    def __init__(self, v):
        self._v = v
    @property
    def value(self):
        return self._v
    def __repr__(self):
        return f"StageName({self._v!r})"


class StageStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs_review"


# ------------------------------------------------------------------
# 模拟对象
# ------------------------------------------------------------------

def make_stage(id="s1", name=StageName.REQUIREMENT_ANALYSIS, status=StageStatus.PENDING,
               order_index=0, expert_type="developer"):
    s = MagicMock()
    s.id = id
    # name 可能是 StageName 枚举或字符串，包装成有 .value 的 mock
    name_mock = MagicMock()
    name_mock.value = name.value if hasattr(name, 'value') else name
    s.name = name_mock
    s.status = status
    s.order_index = order_index
    s.expert_type = expert_type
    s.output = None
    s.review_comment = None
    s.started_at = None
    s.finished_at = None
    return s


def make_workflow(id="wf1", parent_id="p1", title="test workflow",
                  status=WorkflowTaskStatus.CREATED, stages=None):
    wf = MagicMock()
    wf.id = id
    wf.parent_id = parent_id
    wf.title = title
    wf.description = ""
    wf.status = status
    wf.progress = 0
    wf.retries = 0
    wf.stages = stages or [make_stage("s1", StageName.REQUIREMENT_ANALYSIS, StageStatus.PENDING, 0)]
    wf.heartbeat_at = datetime.now(timezone.utc)
    return wf


def make_parent(id="p1", title="test task", status=ParentTaskStatus.DRAFT,
                workflows=None):
    p = MagicMock()
    p.id = id
    p.title = title
    p.description = "test"
    p.status = status
    p.workflow_tasks = workflows or []
    p.plan = None
    return p


@asynccontextmanager
async def mock_session_scope():
    """Mock session_scope that yields a fake session"""
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    yield session


# ------------------------------------------------------------------
# 枚举一致性验证
# ------------------------------------------------------------------

def test_parent_status_enum_completeness():
    assert len({
        ParentTaskStatus.DRAFT, ParentTaskStatus.CONFIRMED,
        ParentTaskStatus.SCHEDULED, ParentTaskStatus.IN_PROGRESS,
        ParentTaskStatus.BLOCKED, ParentTaskStatus.COMPLETED,
        ParentTaskStatus.FAILED
    }) == 7


def test_workflow_status_enum_completeness():
    assert len({
        WorkflowTaskStatus.CREATED, WorkflowTaskStatus.IN_PROGRESS,
        WorkflowTaskStatus.REVIEWING, WorkflowTaskStatus.COMPLETED,
        WorkflowTaskStatus.FAILED, WorkflowTaskStatus.CANCELLED
    }) == 6


def test_stage_status_enum_completeness():
    assert len({
        StageStatus.PENDING, StageStatus.RUNNING, StageStatus.SUCCEEDED,
        StageStatus.FAILED, StageStatus.SKIPPED, StageStatus.NEEDS_REVIEW
    }) == 6


# ------------------------------------------------------------------
# ParentTask 状态聚合
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parent_scheduled_when_all_workflows_created():
    parent = make_parent(status=ParentTaskStatus.CONFIRMED)
    parent.workflow_tasks = [
        make_workflow(status=WorkflowTaskStatus.CREATED),
        make_workflow(status=WorkflowTaskStatus.CREATED),
    ]

    with patch("app.db.repository.update_parent_status", new_callable=AsyncMock) as m:
        with patch("app.db.repository.get_parent_task", new_callable=AsyncMock, return_value=parent):
            from app.engine.workflow_engine import _refresh_parent_status
            await _refresh_parent_status(MagicMock(), parent.id)
            assert m.called


@pytest.mark.asyncio
async def test_parent_in_progress_when_any_workflow_in_progress():
    parent = make_parent(status=ParentTaskStatus.SCHEDULED)
    parent.workflow_tasks = [
        make_workflow(status=WorkflowTaskStatus.COMPLETED),
        make_workflow(status=WorkflowTaskStatus.IN_PROGRESS),
    ]

    with patch("app.db.repository.update_parent_status", new_callable=AsyncMock) as m:
        with patch("app.db.repository.get_parent_task", new_callable=AsyncMock, return_value=parent):
            from app.engine.workflow_engine import _refresh_parent_status
            await _refresh_parent_status(MagicMock(), parent.id)
            assert m.call_args[0][2] == ParentTaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_parent_completed_when_all_workflows_completed():
    parent = make_parent(status=ParentTaskStatus.IN_PROGRESS)
    parent.workflow_tasks = [
        make_workflow(status=WorkflowTaskStatus.COMPLETED),
        make_workflow(status=WorkflowTaskStatus.COMPLETED),
    ]

    with patch("app.db.repository.update_parent_status", new_callable=AsyncMock) as m:
        with patch("app.db.repository.get_parent_task", new_callable=AsyncMock, return_value=parent):
            from app.engine.workflow_engine import _refresh_parent_status
            await _refresh_parent_status(MagicMock(), parent.id)
            assert m.call_args[0][2] == ParentTaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_parent_failed_when_any_workflow_failed():
    parent = make_parent(status=ParentTaskStatus.IN_PROGRESS)
    parent.workflow_tasks = [
        make_workflow(status=WorkflowTaskStatus.COMPLETED),
        make_workflow(status=WorkflowTaskStatus.FAILED),
    ]

    with patch("app.db.repository.update_parent_status", new_callable=AsyncMock) as m:
        with patch("app.db.repository.get_parent_task", new_callable=AsyncMock, return_value=parent):
            from app.engine.workflow_engine import _refresh_parent_status
            await _refresh_parent_status(MagicMock(), parent.id)
            assert m.call_args[0][2] == ParentTaskStatus.FAILED


# ------------------------------------------------------------------
# WorkflowTask 状态流转（mock session_scope 避免真实 DB）
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_created_to_in_progress():
    """工作项入队 → 状态变为 IN_PROGRESS"""
    wf = make_workflow(status=WorkflowTaskStatus.CREATED)
    parent = make_parent(workflows=[wf])
    mock_expert = MagicMock()
    mock_expert.run = AsyncMock(return_value=MagicMock(success=True, summary="done", artifacts={}, approved=None))

    with patch("app.engine.workflow_engine.session_scope", new=mock_session_scope):
        with patch("app.db.repository.get_workflow_task", new_callable=AsyncMock, return_value=wf):
            with patch("app.db.repository.get_parent_task", new_callable=AsyncMock, return_value=parent):
                with patch("app.db.repository.update_workflow_status", new_callable=AsyncMock) as m_wf:
                    with patch("app.db.repository.add_log", new_callable=AsyncMock):
                        with patch("app.infra.bus.event_bus.publish", new_callable=AsyncMock):
                            with patch("app.engine.workflow_engine.create_agent_for_stage", return_value=mock_expert):
                                from app.engine.workflow_engine import WorkflowEngine
                                engine = WorkflowEngine()
                                await engine._process_workflow(wf.id)

                                # 验证状态变为 IN_PROGRESS
                                assert any(
                                    c.kwargs.get("status") == WorkflowTaskStatus.IN_PROGRESS
                                    for c in m_wf.call_args_list
                                )


@pytest.mark.asyncio
async def test_workflow_all_stages_succeeded_to_completed():
    """工作项全部阶段成功 → COMPLETED"""
    wf = make_workflow(status=WorkflowTaskStatus.IN_PROGRESS)
    wf.stages = [
        make_stage("s1", StageName.REQUIREMENT_ANALYSIS, StageStatus.SUCCEEDED, 0),
        make_stage("s2", StageName.TECHNICAL_DESIGN, StageStatus.SUCCEEDED, 1),
    ]

    with patch("app.engine.workflow_engine.session_scope", new=mock_session_scope):
        with patch("app.db.repository.get_workflow_task", new_callable=AsyncMock, return_value=wf):
            with patch("app.db.repository.get_parent_task", new_callable=AsyncMock, return_value=None):
                with patch("app.db.repository.update_workflow_status", new_callable=AsyncMock) as m_wf:
                    with patch("app.db.repository.update_stage", new_callable=AsyncMock):
                        with patch("app.db.repository.add_log", new_callable=AsyncMock):
                            with patch("app.infra.bus.event_bus.publish", new_callable=AsyncMock):
                                from app.engine.workflow_engine import WorkflowEngine
                                engine = WorkflowEngine()
                                await engine._process_workflow(wf.id)

                                assert any(
                                    c.kwargs.get("status") == WorkflowTaskStatus.COMPLETED
                                    for c in m_wf.call_args_list
                                )


@pytest.mark.asyncio
async def test_workflow_max_retries_to_failed():
    """工作项达到最大重试 → FAILED (验证 retries字段累积)"""
    # 这个测试验证：当 expert 返回 success=False 时，retries 字段会累积
    # 达到 max_retries 后状态变为 FAILED
    from app.db.models import WorkflowTaskStatus
    from app.engine.workflow_engine import WorkflowEngine, StageAction

    wf = make_workflow(status=WorkflowTaskStatus.IN_PROGRESS)
    wf.retries = 2 # 已有2次重试
    wf.stages = [
        make_stage("s1", StageName.REQUIREMENT_ANALYSIS, StageStatus.PENDING, 0),
    ]
    mock_expert = MagicMock()
    mock_expert.run = AsyncMock(return_value=MagicMock(
        success=False, summary="fail", artifacts={}, approved=None
    ))

    with patch("app.engine.workflow_engine.session_scope", new=mock_session_scope):
        with patch("app.db.repository.get_workflow_task", new_callable=AsyncMock, return_value=wf):
            with patch("app.db.repository.get_parent_task", new_callable=AsyncMock, return_value=None):
                with patch("app.db.repository.update_workflow_status", new_callable=AsyncMock) as m_wf:
                    with patch("app.db.repository.update_stage", new_callable=AsyncMock):
                        with patch("app.db.repository.add_log", new_callable=AsyncMock):
                            with patch("app.infra.bus.event_bus.publish", new_callable=AsyncMock):
                                with patch("app.config.settings") as mock_settings:
                                    mock_settings.max_retries = 3
                                    with patch("app.engine.workflow_engine.create_agent_for_stage", return_value=mock_expert):
                                        from app.engine.workflow_engine import WorkflowEngine
                                        engine = WorkflowEngine()
                                        # 运行 _process_workflow，它会处理重试逻辑
                                        # 由于 retries=2 < max_retries=3，retries会累加到3然后失败
                                        await engine._process_workflow(wf.id)

                                        # 验证 update_workflow_status 被调用过，且有一次是 FAILED
                                        called_with_failed = any(
                                            c.kwargs.get("status") == WorkflowTaskStatus.FAILED
                                            for c in m_wf.call_args_list
                                        )
                                        assert called_with_failed, f"期望 FAILED，实际调用: {m_wf.call_args_list}"


# ------------------------------------------------------------------
# Stage 状态流转
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stage_pending_to_running():
    """阶段 PENDING → RUNNING（专家开始执行）"""
    stage = make_stage(status=StageStatus.PENDING)
    wf = make_workflow(stages=[stage])

    with patch("app.engine.workflow_engine.session_scope", new=mock_session_scope):
        with patch("app.db.repository.get_workflow_task", new_callable=AsyncMock, return_value=wf):
            with patch("app.db.repository.update_workflow_status", new_callable=AsyncMock):
                with patch("app.db.repository.update_stage", new_callable=AsyncMock) as m_stage:
                    with patch("app.db.repository.add_log", new_callable=AsyncMock):
                        with patch("app.infra.bus.event_bus.publish", new_callable=AsyncMock):
                            mock_expert = MagicMock()
                            mock_expert.run = AsyncMock(return_value=MagicMock(
                                success=True, summary="done", artifacts={}, approved=None
                            ))
                            with patch("app.engine.workflow_engine.create_agent_for_stage", return_value=mock_expert):
                                from app.engine.workflow_engine import WorkflowEngine
                                engine = WorkflowEngine()
                                ok = await engine._run_stage(
                                    workflow_id=wf.id,
                                    stage_id=stage.id,
                                    stage_name=stage.name,
                                    expert_type="developer",
                                    workflow_title=wf.title,
                                    workflow_description="",
                                    parent_description="",
                                )

                                assert ok == StageAction.CONTINUE
                                assert any(
                                    c.kwargs.get("status") == StageStatus.RUNNING
                                    for c in m_stage.call_args_list
                                )


@pytest.mark.asyncio
async def test_stage_running_to_succeeded():
    """阶段 RUNNING → SUCCEEDED（专家成功）"""
    stage = make_stage(status=StageStatus.RUNNING)
    wf = make_workflow(stages=[stage])

    with patch("app.engine.workflow_engine.session_scope", new=mock_session_scope):
        with patch("app.db.repository.get_workflow_task", new_callable=AsyncMock, return_value=wf):
            with patch("app.db.repository.update_workflow_status", new_callable=AsyncMock):
                with patch("app.db.repository.update_stage", new_callable=AsyncMock) as m_stage:
                    with patch("app.db.repository.add_log", new_callable=AsyncMock):
                        with patch("app.infra.bus.event_bus.publish", new_callable=AsyncMock):
                            mock_expert = MagicMock()
                            mock_expert.run = AsyncMock(return_value=MagicMock(
                                success=True, summary="设计完成", artifacts={"design": "..."}, approved=None
                            ))
                            with patch("app.engine.workflow_engine.create_agent_for_stage", return_value=mock_expert):
                                from app.engine.workflow_engine import WorkflowEngine
                                engine = WorkflowEngine()
                                ok = await engine._run_stage(
                                    workflow_id=wf.id,
                                    stage_id=stage.id,
                                    stage_name=StageName.TECHNICAL_DESIGN,
                                    expert_type="developer",
                                    workflow_title=wf.title,
                                    workflow_description="",
                                    parent_description="",
                                )

                                assert ok == StageAction.CONTINUE
                                assert any(
                                    c.kwargs.get("status") == StageStatus.SUCCEEDED
                                    for c in m_stage.call_args_list
                                )


@pytest.mark.asyncio
async def test_stage_running_to_failed():
    """阶段 RUNNING → FAILED（专家失败）"""
    stage = make_stage(status=StageStatus.RUNNING)
    wf = make_workflow(stages=[stage])

    with patch("app.engine.workflow_engine.session_scope", new=mock_session_scope):
        with patch("app.db.repository.get_workflow_task", new_callable=AsyncMock, return_value=wf):
            with patch("app.db.repository.update_workflow_status", new_callable=AsyncMock):
                with patch("app.db.repository.update_stage", new_callable=AsyncMock) as m_stage:
                    with patch("app.db.repository.add_log", new_callable=AsyncMock):
                        with patch("app.infra.bus.event_bus.publish", new_callable=AsyncMock):
                            mock_expert = MagicMock()
                            mock_expert.run = AsyncMock(return_value=MagicMock(
                                success=False, summary="专家执行失败", artifacts={}, approved=None
                            ))
                            with patch("app.engine.workflow_engine.create_agent_for_stage", return_value=mock_expert):
                                from app.engine.workflow_engine import WorkflowEngine
                                engine = WorkflowEngine()
                                ok = await engine._run_stage(
                                    workflow_id=wf.id,
                                    stage_id=stage.id,
                                    stage_name=StageName.TECHNICAL_DESIGN,
                                    expert_type="developer",
                                    workflow_title=wf.title,
                                    workflow_description="",
                                    parent_description="",
                                )

                                assert ok == StageAction.RETRY
                                assert any(
                                    c.kwargs.get("status") == StageStatus.FAILED
                                    for c in m_stage.call_args_list
                                )


@pytest.mark.asyncio
async def test_stage_review_not_approved_resets_to_pending():
    """评审阶段未通过 → 重置为 PENDING（允许重试整个工作项）"""
    stage = make_stage(
        id="s-review",
        name=StageName.TECHNICAL_REVIEW,
        status=StageStatus.RUNNING,
        expert_type="architect"
    )
    wf = make_workflow(stages=[
        make_stage("s1", StageName.REQUIREMENT_ANALYSIS, StageStatus.SUCCEEDED, 0),
        make_stage("s2", StageName.TECHNICAL_DESIGN, StageStatus.SUCCEEDED, 1),
        stage,
    ])

    with patch("app.engine.workflow_engine.session_scope", new=mock_session_scope):
        with patch("app.db.repository.get_workflow_task", new_callable=AsyncMock, return_value=wf):
            with patch("app.db.repository.update_workflow_status", new_callable=AsyncMock):
                with patch("app.db.repository.update_stage", new_callable=AsyncMock) as m_stage:
                    with patch("app.db.repository.add_log", new_callable=AsyncMock):
                        with patch("app.infra.bus.event_bus.publish", new_callable=AsyncMock):
                            mock_expert = MagicMock()
                            mock_expert.run = AsyncMock(return_value=MagicMock(
                                success=True, summary="评审未通过", artifacts={}, approved=False,
                                review_comment="设计不符合要求"
                            ))
                            with patch("app.engine.workflow_engine.create_agent_for_stage", return_value=mock_expert):
                                from app.engine.workflow_engine import WorkflowEngine
                                engine = WorkflowEngine()
                                ok = await engine._run_stage(
                                    workflow_id=wf.id,
                                    stage_id=stage.id,
                                    stage_name=StageName.TECHNICAL_REVIEW,
                                    expert_type="architect",
                                    workflow_title=wf.title,
                                    workflow_description="",
                                    parent_description="",
                                )

                                assert ok == StageAction.RETRY
                                # 评审失败 → 重置为 PENDING（让外层重试整个工作项）
                                assert any(
                                    c.kwargs.get("status") == StageStatus.PENDING
                                    for c in m_stage.call_args_list
                                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])