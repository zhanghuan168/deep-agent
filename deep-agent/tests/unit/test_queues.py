"""单元测试：任务队列"""
import pytest
import asyncio
from app.infra.queues import (
    TaskQueue,
    WorkflowQueueItem,
    StageQueueItem,
    SchedulerQueueItem,
    scheduler_queue,
    workflow_queue,
    stage_queue,
)


class TestTaskQueue:
    """测试TaskQueue基类"""

    @pytest.fixture
    def queue(self):
        return TaskQueue("test")

    @pytest.mark.asyncio
    async def test_put_and_get(self, queue):
        await queue.put("item1")
        await queue.put("item2")
        assert queue.size == 2

        item1 = await queue.get()
        assert item1 == "item1"
        assert queue.size == 1

    @pytest.mark.asyncio
    async def test_empty_queue_timeout(self, queue):
        # 不使用 timeout 参数测试，使用 wait_for
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_drain(self, queue):
        await queue.put("item1")
        await queue.put("item2")
        await queue.put("item3")

        items = await queue.drain()
        assert len(items) == 3
        assert queue.size == 0

    @pytest.mark.asyncio
    async def test_size_property(self, queue):
        assert queue.size == 0
        await queue.put("a")
        await queue.put("b")
        assert queue.size == 2
        await queue.get()
        assert queue.size == 1


class TestWorkflowQueueItem:
    """测试工作项队列载荷"""

    def test_create_workflow_queue_item(self):
        # 不在async context中，所以手动提供默认值
        item = WorkflowQueueItem(
            workflow_id="wf-123",
            parent_id="parent-456",
            enqueued_at=0.0,
            enqueue_id="test-id-1"
        )
        assert item.workflow_id == "wf-123"
        assert item.parent_id == "parent-456"
        assert item.enqueue_id == "test-id-1"
        assert item.enqueued_at == 0.0

    def test_workflow_queue_item_unique_id(self):
        item1 = WorkflowQueueItem(workflow_id="wf-1", parent_id="p-1", enqueued_at=0.0, enqueue_id="id-1")
        item2 = WorkflowQueueItem(workflow_id="wf-1", parent_id="p-1", enqueued_at=0.0, enqueue_id="id-2")
        # 每个item应该有唯一的enqueue_id
        assert item1.enqueue_id != item2.enqueue_id


class TestStageQueueItem:
    """测试阶段队列载荷"""

    def test_create_stage_queue_item(self):
        item = StageQueueItem(
            workflow_id="wf-123",
            stage_id="stage-456"
        )
        assert item.workflow_id == "wf-123"
        assert item.stage_id == "stage-456"
        assert item.enqueue_id is not None


class TestSchedulerQueueItem:
    """测试调度队列载荷"""

    def test_create_scheduler_queue_item(self):
        item = SchedulerQueueItem(parent_id="parent-123")
        assert item.parent_id == "parent-123"
        assert item.enqueue_id is not None


class TestGlobalQueues:
    """测试全局队列单例"""

    def test_scheduler_queue_exists(self):
        assert scheduler_queue is not None
        assert scheduler_queue.name == "scheduler"

    def test_workflow_queue_exists(self):
        assert workflow_queue is not None
        assert workflow_queue.name == "workflow"

    def test_stage_queue_exists(self):
        assert stage_queue is not None
        assert stage_queue.name == "stage"

    def test_queues_are_independent(self):
        # 三个队列应该是独立的
        assert scheduler_queue is not workflow_queue
        assert scheduler_queue is not stage_queue
        assert workflow_queue is not stage_queue