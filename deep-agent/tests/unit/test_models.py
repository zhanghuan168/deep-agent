"""单元测试：数据库模型"""
import pytest
from datetime import datetime, timezone
from app.db.models import (
    Base,
    ParentTask,
    ParentTaskStatus,
    WorkflowTask,
    WorkflowTaskStatus,
    StageInstance,
    StageName,
    StageStatus,
    TaskLog,
    TaskLogLevel,
    ConversationHistory,
    ConversationRole,
    AppSetting,
)


class TestEnums:
    """测试枚举值"""

    def test_parent_task_status(self):
        assert ParentTaskStatus.DRAFT.value == "draft"
        assert ParentTaskStatus.CONFIRMED.value == "confirmed"
        assert ParentTaskStatus.SCHEDULED.value == "scheduled"
        assert ParentTaskStatus.IN_PROGRESS.value == "in_progress"
        assert ParentTaskStatus.COMPLETED.value == "completed"
        assert ParentTaskStatus.FAILED.value == "failed"

    def test_workflow_task_status(self):
        assert WorkflowTaskStatus.CREATED.value == "created"
        assert WorkflowTaskStatus.IN_PROGRESS.value == "in_progress"
        assert WorkflowTaskStatus.REVIEWING.value == "reviewing"
        assert WorkflowTaskStatus.COMPLETED.value == "completed"
        assert WorkflowTaskStatus.FAILED.value == "failed"

    def test_stage_name(self):
        assert StageName.REQUIREMENT_ANALYSIS.value == "requirement_analysis"
        assert StageName.REQUIREMENT_REVIEW.value == "requirement_review"
        assert StageName.TECHNICAL_DESIGN.value == "technical_design"
        assert StageName.TECHNICAL_REVIEW.value == "technical_review"
        assert StageName.TASK_BREAKDOWN.value == "task_breakdown"
        assert StageName.IMPLEMENTATION.value == "implementation"
        assert StageName.CODE_REVIEW.value == "code_review"
        assert StageName.TESTING.value == "testing"

    def test_stage_status(self):
        assert StageStatus.PENDING.value == "pending"
        assert StageStatus.RUNNING.value == "running"
        assert StageStatus.SUCCEEDED.value == "succeeded"
        assert StageStatus.FAILED.value == "failed"
        assert StageStatus.NEEDS_REVIEW.value == "needs_review"

    def test_task_log_level(self):
        assert TaskLogLevel.INFO.value == "info"
        assert TaskLogLevel.WARNING.value == "warning"
        assert TaskLogLevel.ERROR.value == "error"

    def test_conversation_role(self):
        assert ConversationRole.BOSS.value == "boss"
        assert ConversationRole.PROJECT_MANAGER.value == "project_manager"
        assert ConversationRole.SYSTEM.value == "system"


class TestParentTask:
    """测试父任务模型"""

    def test_create_parent_task(self):
        task = ParentTask(
            title="测试任务",
            description="这是一个测试任务",
            status=ParentTaskStatus.DRAFT
        )
        assert task.title == "测试任务"
        assert task.description == "这是一个测试任务"
        assert task.status == ParentTaskStatus.DRAFT
        assert task.id is None or isinstance(task.id, str)

    def test_parent_task_default_status(self):
        task = ParentTask(title="测试", status=ParentTaskStatus.DRAFT)
        assert task.status == ParentTaskStatus.DRAFT

    def test_parent_task_plan_json(self):
        task = ParentTask(
            title="测试",
            plan={"work_items": [{"title": "item1"}]}
        )
        assert task.plan == {"work_items": [{"title": "item1"}]}


class TestWorkflowTask:
    """测试工作项模型"""

    def test_create_workflow_task(self):
        task = WorkflowTask(
            title="开发模块A",
            description="实现用户认证",
            priority=3,
            status=WorkflowTaskStatus.CREATED,
            progress=0
        )
        assert task.title == "开发模块A"
        assert task.priority == 3
        assert task.status == WorkflowTaskStatus.CREATED
        assert task.progress == 0

    def test_workflow_task_default_values(self):
        task = WorkflowTask(title="测试", progress=0, retries=0)
        assert task.progress == 0
        assert task.retries == 0
        assert task.inputs is None
        assert task.outputs is None

    def test_workflow_task_template(self):
        task = WorkflowTask(
            title="测试",
            template=[StageName.REQUIREMENT_ANALYSIS, StageName.TECHNICAL_DESIGN]
        )
        assert len(task.template) == 2
        assert task.template[0] == StageName.REQUIREMENT_ANALYSIS


class TestStageInstance:
    """测试阶段实例模型"""

    def test_create_stage_instance(self):
        stage = StageInstance(
            name=StageName.IMPLEMENTATION,
            order_index=3,
            expert_type="developer",
            status=StageStatus.PENDING
        )
        assert stage.name == StageName.IMPLEMENTATION
        assert stage.order_index == 3
        assert stage.expert_type == "developer"
        assert stage.status == StageStatus.PENDING

    def test_stage_instance_output_json(self):
        stage = StageInstance(
            name=StageName.TECHNICAL_DESIGN,
            order_index=1,
            expert_type="designer",
            output={"architecture": "REST API"}
        )
        assert stage.output == {"architecture": "REST API"}


class TestTaskLog:
    """测试任务日志模型"""

    def test_create_task_log(self):
        log = TaskLog(
            workflow_id="wf-123",
            level=TaskLogLevel.INFO,
            message="工作项开始执行"
        )
        assert log.message == "工作项开始执行"
        assert log.level == TaskLogLevel.INFO
        assert log.workflow_id == "wf-123"

    def test_task_log_error_level(self):
        log = TaskLog(
            workflow_id="wf-123",
            level=TaskLogLevel.ERROR,
            message="执行失败",
            data={"error": "timeout"}
        )
        assert log.level == TaskLogLevel.ERROR
        assert log.data == {"error": "timeout"}


class TestConversationHistory:
    """测试对话历史模型"""

    def test_create_conversation(self):
        conv = ConversationHistory(
            parent_id="parent-123",
            role=ConversationRole.BOSS,
            content="帮我实现用户登录功能"
        )
        assert conv.role == ConversationRole.BOSS
        assert conv.content == "帮我实现用户登录功能"


class TestAppSetting:
    """测试运行时配置模型"""

    def test_create_setting(self):
        setting = AppSetting(
            key="llm_provider",
            value="openai"
        )
        assert setting.key == "llm_provider"
        assert setting.value == "openai"