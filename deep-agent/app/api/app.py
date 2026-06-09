"""FastAPI 应用入口。"""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agents import expert_pool, register_default_experts
from app.api import routes
from app.api.ws import hub
from app.config import settings
from app.db.session import init_db
from app.engine import HeartbeatMonitor, Scheduler, WorkflowEngine
from app.infra import event_bus
from app.infra.bus import Events
from app.logging import logger, setup_logging


# 后台协程句柄
_bg_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("=== 智能项目管理 Agent 系统 启动 ===")

    # 1. 初始化数据库
    await init_db()

    # 2. 初始化默认评审模板
    from app.db.session import session_scope
    async with session_scope() as session:
        from app.db import repository as repo
        await repo.seed_default_review_templates(session)

    # 3. 注册专家
    register_default_experts(expert_pool)

    # 3. 启动后台协程
    scheduler = Scheduler()
    engine = WorkflowEngine(max_concurrent=4)
    monitor = HeartbeatMonitor()

    # 把事件总线里的事件转发到 WebSocket
    async def forward(event_type: str):
        async def cb(data):
            await hub.broadcast(event_type, data)

        event_bus.subscribe(event_type, cb)

    for evt in [
        Events.PARENT_STATUS,
        Events.PARENT_CREATED,
        Events.PARENT_SCHEDULED,
        Events.PARENT_CONFIRMED,
        Events.WORKFLOW_CREATED,
        Events.WORKFLOW_STATUS,
        Events.WORKFLOW_PROGRESS,
        Events.STAGE_STATUS,
        Events.STAGE_REVIEW_NEEDED,
        Events.CHAT_MESSAGE,
        Events.SYSTEM,
    ]:
        await forward(evt)

    _bg_tasks.append(asyncio.create_task(scheduler.run_forever(), name="scheduler"))
    _bg_tasks.append(asyncio.create_task(engine.run_forever(), name="workflow_engine"))
    _bg_tasks.append(asyncio.create_task(monitor.run_forever(), name="heartbeat"))

    logger.info("后台协程已启动: {}", [t.get_name() for t in _bg_tasks])

    yield

    # 关闭
    logger.info("正在关闭...")
    scheduler.stop()
    engine.stop()
    monitor.stop()
    for t in _bg_tasks:
        t.cancel()
    for t in _bg_tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t
    logger.info("=== 系统已关闭 ===")


def create_app() -> FastAPI:
    app = FastAPI(
        title="智能项目管理 Agent",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 防止浏览器/中间代理缓存 HTML/JS/CSS（开发期必备）
    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.endswith((".html", ".js", ".css")) or path == "/":
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.include_router(routes.router, prefix="/api")

    # 静态前端（如果存在）
    static_dir = Path(settings.static_dir)
    if static_dir.exists():
        # 如果有 assets 目录则挂载（Vite 构建产物），没有就跳过
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount(
                "/assets",
                StaticFiles(directory=assets_dir),
                name="assets",
            )

        @app.get("/")
        async def index():
            return FileResponse(static_dir / "index.html")

        @app.get("/{path:path}")
        async def spa_fallback(path: str):
            # 优先匹配静态文件
            target = static_dir / path
            if target.is_file():
                return FileResponse(target)
            # 否则返回 index.html（SPA 路由）
            return FileResponse(static_dir / "index.html")
    else:

        @app.get("/")
        async def index_no_frontend():
            return JSONResponse(
                {
                    "name": "智能项目管理 Agent",
                    "version": "2.0.0",
                    "frontend": "未构建，请先在 frontend/ 目录构建前端",
                    "endpoints": [
                        "/api/health",
                        "/api/chat",
                        "/api/parents",
                        "/api/ws",
                    ],
                }
            )

    return app


# uvicorn 入口
app = create_app()
