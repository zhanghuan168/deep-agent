"""SQLite 异步引擎与会话管理。"""
from __future__ import annotations

import contextlib
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.db.models import Base
from app.logging import logger


def _make_engine(url: str) -> AsyncEngine:
    """创建一个支持 WAL 的异步 SQLite 引擎。"""
    return create_async_engine(
        url,
        echo=False,
        future=True,
        # aiosqlite 单连接即可；WAL 模式在 connect 钩子里开启
        connect_args={"check_same_thread": False},
    )


# 强制使用 POSIX 路径风格，aiosqlite 内部需要
db_url = f"sqlite+aiosqlite:///{Path(settings.db_path).as_posix()}"
engine: AsyncEngine = _make_engine(db_url)
SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def init_db() -> None:
    """初始化数据库（创建表 + 开启 WAL）。"""
    async with engine.begin() as conn:
        # 先开启 WAL 模式与外键
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库初始化完成: {}", settings.db_path)


async def reset_db() -> None:
    """重置数据库（删表重建），仅用于开发/测试。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    logger.warning("数据库已重置")


@contextlib.asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """事务性 session 上下文，异常自动回滚。"""
    session = SessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖。"""
    async with session_scope() as s:
        yield s
