#!/usr/bin/env python3
"""
集成测试：创建真实任务，验证状态机全流程
- 建DB → 创建ParentTask → 创建WorkflowTask → 运行Engine → 验证状态
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.db.session import init_db, SessionLocal
from app.db.models import (
    ParentTask, ParentTaskStatus,
    WorkflowTask, WorkflowTaskStatus,
    StageInstance, StageName, StageStatus,
    TaskLog, TaskLogLevel,
)
from app.engine.workflow_engine import WorkflowEngine
from app.agents.experts import register_default_experts
from app.agents.pool import expert_pool
from sqlalchemy import select


async def main():
    print("=== deep-agent 真实任务流程测试 ===\n")

    # 1. 初始化数据库
    print("1. 初始化数据库...")
    await init_db()
    print("   ✅ 数据库初始化完成\n")

    # 使用独立session创建数据
    session = SessionLocal()
    try:
        # 2. 创建父任务
        print("2. 创建父任务 ParentTask...")
        parent = ParentTask(
            title="实现用户登录模块",
            description="包含用户名密码登录和token管理",
            status=ParentTaskStatus.DRAFT,
        )
        session.add(parent)
        await session.flush()
        parent_id = parent.id
        print(f"   ✅ 父任务创建: id={parent_id}, status={parent.status}\n")

        # 3. 创建工作项
        print("3. 创建工作项 WorkflowTask...")
        workflow = WorkflowTask(
            parent_id=parent_id,
            title="需求分析阶段",
            description="分析登录模块的需求并产出设计文档",
            status=WorkflowTaskStatus.CREATED,
            progress=0,
        )
        session.add(workflow)
        await session.flush()
        workflow_id = workflow.id
        print(f"   ✅ 工作项创建: id={workflow_id}, status={workflow.status}\n")

        # 4. 创建阶段（使用正确的 expert_type）
        print("4. 创建阶段 StageInstance...")
        stages = [
            StageInstance(
                workflow_id=workflow_id,
                name=StageName.REQUIREMENT,
                expert_type="requirement_analyst",
                status=StageStatus.PENDING,
                order_index=0,
            ),
            StageInstance(
                workflow_id=workflow_id,
                name=StageName.DESIGN,
                expert_type="designer",
                status=StageStatus.PENDING,
                order_index=1,
            ),
            StageInstance(
                workflow_id=workflow_id,
                name=StageName.DEVELOPMENT,
                expert_type="developer",
                status=StageStatus.PENDING,
                order_index=2,
            ),
        ]
        for s in stages:
            session.add(s)

        # 5. 添加日志
        log = TaskLog(
            parent_id=parent_id,
            level=TaskLogLevel.INFO,
            message="任务已创建，等待调度",
        )
        session.add(log)
        await session.commit()
        print(f"   ✅ 创建了 {len(stages)} 个阶段\n")

        # 6. 验证初始状态
        print("6. 验证初始状态...")
        parent_stored = await session.get(ParentTask, parent_id)
        wf_stored = await session.get(WorkflowTask, workflow_id)
        print(f"   父任务状态: {parent_stored.status}")
        print(f"   工作项状态: {wf_stored.status}")
        assert parent_stored.status == ParentTaskStatus.DRAFT
        assert wf_stored.status == WorkflowTaskStatus.CREATED
        print("   ✅ 初始状态验证通过\n")

    finally:
        await session.close()

    # 7. 注册专家
    print("7. 注册专家...")
    register_default_experts(expert_pool)
    print(f"   ✅ 已注册专家: {expert_pool.types()}\n")

    # 8. 运行工作流引擎
    print("8. 运行工作流引擎...")
    wf_engine = WorkflowEngine()
    await wf_engine._process_workflow(workflow_id)
    print("   ✅ 引擎处理完成\n")

    # 9. 验证最终状态（用新session避免缓存问题）
    print("9. 验证最终状态...")
    session2 = SessionLocal()
    try:
        wf_final = await session2.get(WorkflowTask, workflow_id)
        parent_final = await session2.get(ParentTask, parent_id)

        print(f"   工作项最终状态: {wf_final.status}")
        print(f"   父任务最终状态: {parent_final.status}")
        print(f"   工作项进度: {wf_final.progress}%")

        # 查询阶段
        stages_q = await session2.execute(
            select(StageInstance).where(StageInstance.workflow_id == workflow_id).order_by(StageInstance.order_index)
        )
        stages_result = stages_q.scalars().all()
        print(f"   阶段数量: {len(stages_result)}")
        for s in stages_result:
            print(f"     - {s.name}: {s.status}")

        # 10. 验证状态机正确性
        print("\n10. 验证状态机正确性...")
        assert wf_final.status == WorkflowTaskStatus.COMPLETED, f"期望 COMPLETED, 实际 {wf_final.status}"
        assert parent_final.status == ParentTaskStatus.COMPLETED, f"期望 COMPLETED, 实际 {parent_final.status}"
        assert wf_final.progress == 100, f"期望 progress=100, 实际 {wf_final.progress}"
        assert len(stages_result) == 3, f"期望 3 个阶段, 实际 {len(stages_result)}"
        for s in stages_result:
            assert s.status == StageStatus.SUCCEEDED, f"阶段 {s.name} 期望 SUCCEEDED, 实际 {s.status}"
        print("   ✅ 状态机验证通过")

        # 查询日志
        print("\n11. 查看任务日志...")
        logs_q = await session2.execute(
            select(TaskLog).where(TaskLog.parent_id == parent_id).order_by(TaskLog.created_at)
        )
        logs = logs_q.scalars().all()
        for log_entry in logs:
            print(f"   [{log_entry.level.value}] {log_entry.message}")

        print("\n=== 测试完成: 全部通过 ===")

    finally:
        await session2.close()


if __name__ == "__main__":
    asyncio.run(main())