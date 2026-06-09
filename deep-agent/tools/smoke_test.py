"""端到端 smoke test：
1. 启动 FastAPI 服务
2. 用 TestClient 模拟老板下达需求
3. 跑完整流程：聊天 → 确认 → 调度 → 流程引擎 → 完成
4. 验证最终状态
"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 用临时 DB 避免污染
import os
os.environ["DAGENT_DB_PATH"] = str(ROOT / "data" / "smoke.db")
if os.path.exists(os.environ["DAGENT_DB_PATH"]):
    os.remove(os.environ["DAGENT_DB_PATH"])

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.db.session import init_db
from app.db import repository as repo
from app.db.session import session_scope
from app.db.models import ParentTaskStatus, WorkflowTaskStatus, StageStatus


async def _wait_until(predicate, timeout=30.0, interval=0.5):
    """轮询直到 predicate 返回真或超时。"""
    start = time.time()
    while time.time() - start < timeout:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    return None


async def smoke():
    # 初始化数据库
    await init_db()

    app = create_app()
    with TestClient(app) as client:
        # 1. 健康检查
        r = client.get("/api/health")
        assert r.status_code == 200, r.text
        print("[1] health:", r.json())

        # 2. 老板下达需求
        r = client.post(
            "/api/chat",
            json={"message": "做一个能记录每日开支并按月统计的小程序。分前后端，要有登录、记账、统计、报表四个功能。"},
        )
        assert r.status_code == 200, r.text
        chat1 = r.json()
        parent_id = chat1["parent_id"]
        plan = chat1["data"]["plan"]
        print(f"[2] created parent: {parent_id}, work_items={len(plan.get('work_items', []))}")
        assert plan.get("work_items"), "规划器应该产出 work_items"

        # 3. 老板确认
        r = client.post(
            "/api/chat/confirm",
            json={"parent_id": parent_id, "plan": plan},
        )
        assert r.status_code == 200, r.text
        print("[3] plan confirmed")

        # 4. 等待工作项执行完毕（最多 60s，因为有 5 个工作项 × 7 个阶段）
        async def check_done():
            async with session_scope() as s:
                parent = await repo.get_parent_task(s, parent_id)
                if parent is None:
                    return None
                statuses = {w.status for w in parent.workflow_tasks}
                if all(st == WorkflowTaskStatus.COMPLETED for st in statuses) and len(statuses) > 0:
                    return parent
                return None

        result = await _wait_until(check_done, timeout=120.0, interval=1.0)
        if result is None:
            async with session_scope() as s:
                parent = await repo.get_parent_task(s, parent_id)
                print("[!] timeout. parent status:", parent.status)
                for w in parent.workflow_tasks:
                    print(f"    - {w.title}: {w.status} (progress={w.progress}, err={w.last_error})")
                    for st in w.stages:
                        print(f"      · {st.name.value}: {st.status} comment={st.review_comment}")
            sys.exit(1)
        print(f"[4] all workflows done! parent.status = {result.status.value}")
        for w in result.workflow_tasks:
            print(f"    - {w.title}: {w.status.value} progress={w.progress}% stages={[s.status.value for s in w.stages]}")

        # 5. 校验
        assert result.status == ParentTaskStatus.COMPLETED, f"父任务应为 COMPLETED，实际 {result.status}"
        for w in result.workflow_tasks:
            assert w.status == WorkflowTaskStatus.COMPLETED
            for s in w.stages:
                assert s.status in (StageStatus.SUCCEEDED, StageStatus.SKIPPED), f"阶段 {s.name.value} 状态异常: {s.status}"

        # 6. 日志/对话记录
        async with session_scope() as s:
            logs = await repo.list_logs(s, parent_id=parent_id, limit=20)
            conv = await repo.list_conversations(s, parent_id)
        print(f"[5] logs={len(logs)} conversations={len(conv)}")
        assert len(logs) > 0
        assert len(conv) >= 2  # 至少老板 + 项目经理 各一条

    print("\n=== smoke test PASSED ===")


if __name__ == "__main__":
    asyncio.run(smoke())
