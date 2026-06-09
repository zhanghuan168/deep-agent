"""Test if new inserts work correctly with Chinese."""
import asyncio
from app.db.session import session_scope
from app.db import repository as repo
from app.db.models import ParentTaskStatus


async def main():
    async with session_scope() as s:
        p = await repo.create_parent_task(
            s,
            title="测试中文标题",
            description="做一个会记账的微信小程序",
            plan={"work_items": [{"title": "需求分析", "description": "分析记账功能"}]},
            status=ParentTaskStatus.DRAFT,
        )
        print(f"created: {p.id}")
        print(f"title: {p.title!r}")
        print(f"desc: {p.description!r}")
        print(f"plan: {p.plan}")
    # 重新读
    async with session_scope() as s:
        p2 = await repo.get_parent_task(s, p.id)
        print(f"\nre-read title: {p2.title!r}")
        print(f"re-read desc: {p2.description!r}")

asyncio.run(main())
