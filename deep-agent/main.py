"""项目入口（直接 `python main.py` 启动）。

也可以：`uvicorn main:app --reload`
"""
from __future__ import annotations

import io
import sys


def _force_utf8_stdio() -> None:
    """Windows 上 Python 默认 stdout 编码是 GBK，会让 cmd 窗口里中文乱码。
    必须在所有 import 之前执行。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            try:
                buf = stream.buffer  # type: ignore[attr-defined]
                setattr(
                    sys,
                    name,
                    io.TextIOWrapper(buf, encoding="utf-8", errors="replace", line_buffering=True),
                )
            except Exception:
                pass


_force_utf8_stdio()

import uvicorn  # noqa: E402

from app.api.app import app  # noqa: F401, E402  供外部 import
from app.config import settings  # noqa: E402


def main() -> None:
    uvicorn.run(
        "app.api.app:app",
        host=settings.host,
        port=settings.port,
        log_level="debug",
        reload=False,
    )


if __name__ == "__main__":
    main()
