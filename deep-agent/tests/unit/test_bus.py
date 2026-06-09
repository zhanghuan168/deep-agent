"""单元测试：事件总线"""
import pytest
import asyncio
from app.infra.bus import EventBus, Events, event_bus


class TestEventBus:
    """测试事件总线"""

    @pytest.fixture
    def bus(self):
        return EventBus()

    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self, bus):
        received = []

        async def callback(data):
            received.append(data)

        bus.subscribe("test.event", callback)
        await bus.publish("test.event", {"key": "value"})

        assert len(received) == 1
        assert received[0] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, bus):
        received1 = []
        received2 = []

        async def cb1(data):
            received1.append(data)

        async def cb2(data):
            received2.append(data)

        bus.subscribe("test.event", cb1)
        bus.subscribe("test.event", cb2)
        await bus.publish("test.event", {"msg": "hello"})

        assert len(received1) == 1
        assert len(received2) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus):
        received = []

        async def callback(data):
            received.append(data)

        bus.subscribe("test.event", callback)
        bus.unsubscribe("test.event", callback)
        await bus.publish("test.event", {"msg": "hello"})

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self, bus):
        # 发布到没有订阅者的事件不应该报错
        await bus.publish("nonexistent.event", {"data": 123})
        # 没有异常就算通过

    @pytest.mark.asyncio
    async def test_sync_callback(self, bus):
        """测试同步回调（不返回协程）"""
        received = []

        def callback(data):
            received.append(data)

        bus.subscribe("sync.event", callback)
        await bus.publish("sync.event", {"sync": True})

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_subscriber_exception_doesnt_break_others(self, bus):
        """一个订阅者抛错不应该影响其他订阅者"""
        errors = []

        async def bad_callback(data):
            raise RuntimeError("callback error")

        async def good_callback(data):
            pass

        bus.subscribe("error.event", bad_callback)
        bus.subscribe("error.event", good_callback)
        
        # 不应该抛出异常
        await bus.publish("error.event", {})

    def test_subscribers_list(self, bus):
        async def cb1(data): pass
        async def cb2(data): pass

        bus.subscribe("list.event", cb1)
        bus.subscribe("list.event", cb2)

        subs = bus.subscribers("list.event")
        assert len(subs) == 2
        assert cb1 in subs
        assert cb2 in subs

    def test_subscribers_empty(self, bus):
        subs = bus.subscribers("empty.event")
        assert len(subs) == 0


class TestEvents:
    """测试事件类型常量"""

    def test_parent_events(self):
        assert Events.PARENT_CREATED == "parent.created"
        assert Events.PARENT_CONFIRMED == "parent.confirmed"
        assert Events.PARENT_SCHEDULED == "parent.scheduled"
        assert Events.PARENT_STATUS == "parent.status"

    def test_workflow_events(self):
        assert Events.WORKFLOW_CREATED == "workflow.created"
        assert Events.WORKFLOW_STATUS == "workflow.status"
        assert Events.WORKFLOW_PROGRESS == "workflow.progress"
        assert Events.WORKFLOW_LOG == "workflow.log"

    def test_stage_events(self):
        assert Events.STAGE_STATUS == "stage.status"
        assert Events.STAGE_REVIEW_NEEDED == "stage.review_needed"

    def test_chat_events(self):
        assert Events.CHAT_MESSAGE == "chat.message"
        assert Events.SYSTEM == "system"


class TestGlobalEventBus:
    """测试全局事件总线单例"""

    def test_global_event_bus_exists(self):
        assert event_bus is not None
        assert isinstance(event_bus, EventBus)

    def test_global_bus_independent(self):
        """全局bus应该和其他实例独立"""
        local_bus = EventBus()
        local_bus.subscribe("global.test", lambda d: None)
        
        # global bus不应该有local_bus的订阅
        global_subs = event_bus.subscribers("global.test")
        local_subs = local_bus.subscribers("global.test")
        
        assert len(global_subs) == 0
        assert len(local_subs) == 1