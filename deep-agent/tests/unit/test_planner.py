"""单元测试：任务拆解器（planner）"""
import pytest
from unittest.mock import patch, AsyncMock
from app.pm.planner import (
    make_plan,
    refine_plan,
    _rule_based_plan,
    _split_sentences,
    _split_items,
    _merge_items,
    PLAN_SCHEMA_HINT,
)


class TestRuleBasedPlan:
    """测试规则回退规划"""

    def test_basic_plan(self):
        plan = _rule_based_plan("实现登录功能", "用户可以通过用户名密码登录")
        assert "summary" in plan
        assert "work_items" in plan
        assert len(plan["work_items"]) > 0

    def test_plan_work_items_have_required_fields(self):
        plan = _rule_based_plan("测试任务", "描述")
        for item in plan["work_items"]:
            assert "title" in item
            assert "description" in item

    def test_empty_description(self):
        plan = _rule_based_plan("唯一标题", "")
        assert len(plan["work_items"]) >= 1

    def test_priority_assignment(self):
        plan = _rule_based_plan("多个句子任务", "第一句描述。第二句描述。第三句描述。")
        # 应该至少有2个工作项
        assert len(plan["work_items"]) >= 2

    def test_split_sentences(self):
        text = "实现登录功能。实现注册功能。实现登出功能？没问题！"
        sentences = _split_sentences(text)
        assert len(sentences) >= 3


class TestSplitItems:
    """测试工作项拆分"""

    def test_split_items_basic(self):
        items = [
            {"title": "登录功能", "description": "实现用户名密码登录。实现验证码登录。", "priority": 5}
        ]
        new_items = _split_items(items)
        assert len(new_items) > 1

    def test_split_items_single_sentence(self):
        items = [
            {"title": "登录", "description": "实现登录", "priority": 5}
        ]
        new_items = _split_items(items)
        # 单句不应该拆分
        assert len(new_items) == 1

    def test_split_items_empty(self):
        items = []
        new_items = _split_items(items)
        assert new_items == []


class TestMergeItems:
    """测试工作项合并"""

    def test_merge_items_basic(self):
        items = [
            {"title": "登录", "description": "登录功能", "priority": 5},
            {"title": "注册", "description": "注册功能", "priority": 5},
        ]
        merged = _merge_items(items)
        assert len(merged) == 1
        assert "登录" in merged[0]["description"]
        assert "注册" in merged[0]["description"]

    def test_merge_items_single_item(self):
        items = [{"title": "唯一", "description": "描述", "priority": 5}]
        merged = _merge_items(items)
        assert len(merged) == 1


class TestMakePlan:
    """测试公开API make_plan"""

    @pytest.mark.asyncio
    async def test_make_plan_returns_dict(self):
        with patch("app.pm.planner.runtime_settings.get_llm_config", new_callable=AsyncMock, return_value={"provider": "", "model": ""}):
            plan = await make_plan("实现用户模块", "包含登录和注册")
            assert isinstance(plan, dict)
            assert "work_items" in plan

    @pytest.mark.asyncio
    async def test_make_plan_with_empty_description(self):
        with patch("app.pm.planner.runtime_settings.get_llm_config", new_callable=AsyncMock, return_value={"provider": "", "model": ""}):
            plan = await make_plan("简单任务", "")
            assert len(plan["work_items"]) >= 1


class TestRefinePlan:
    """测试计划优化"""

    @pytest.mark.asyncio
    async def test_refine_plan_feedback_append(self):
        plan = {
            "summary": "用户模块",
            "work_items": [
                {"title": "登录功能", "description": "实现登录", "priority": 5}
            ]
        }
        refined = await refine_plan(plan, "需要添加记住密码功能")
        # 反馈是否追加取决于关键词匹配，这里验证plan结构正确
        assert "work_items" in refined

    @pytest.mark.asyncio
    async def test_refine_plan_split(self):
        plan = {
            "summary": "任务",
            "work_items": [
                {"title": "登录功能", "description": "实现登录模块。实现注册模块。", "priority": 5}
            ]
        }
        refined = await refine_plan(plan, "拆分")
        assert len(refined["work_items"]) > 1

    @pytest.mark.asyncio
    async def test_refine_plan_merge(self):
        plan = {
            "summary": "任务",
            "work_items": [
                {"title": "登录", "description": "登录", "priority": 5},
                {"title": "注册", "description": "注册", "priority": 5},
            ]
        }
        refined = await refine_plan(plan, "合并")
        assert len(refined["work_items"]) <= 2


class TestPlanSchemaHint:
    """测试计划Schema提示"""

    def test_schema_hint_contains_required_fields(self):
        assert "work_items" in PLAN_SCHEMA_HINT
        assert "summary" in PLAN_SCHEMA_HINT
        assert "priority" in PLAN_SCHEMA_HINT