"""统一日志（loguru）"""
from __future__ import annotations

import io
import sys

from loguru import logger

from app.config import settings


def _ensure_utf8_stdout() -> None:
    """Windows 上 Python 默认 stdout 编码是 GBK，导致 cmd 窗口里中文乱码。
    把 stdout/stderr 强制切成 UTF-8（如果还没有的话）。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        enc = getattr(stream, "encoding", None) or ""
        if enc.lower().replace("-", "") != "utf8":
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            except (AttributeError, ValueError):
                # 旧 Python / 重定向情况：用 TextIOWrapper 兜底
                try:
                    buf = stream.buffer  # type: ignore[attr-defined]
                    new_stream = io.TextIOWrapper(
                        buf, encoding="utf-8", errors="replace", line_buffering=True
                    )
                    setattr(sys, stream_name, new_stream)
                except Exception:
                    pass


def setup_logging() -> None:
    """配置全局日志输出。"""
    _ensure_utf8_stdout()
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        colorize=True,
        format="<green>{time:HH:mm:ss.SSS}</green> | "
        "<level>{level: <7}</level> | "
        "<cyan>{name}:{function}:{line}</cyan> - "
        "<level>{message}</level>",
    )
    # 文件日志
    log_path = settings.data_dir / "dagent.log"
    logger.add(
        str(log_path),
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
        enqueue=True,
    )


__all__ = ["logger", "setup_logging"]
