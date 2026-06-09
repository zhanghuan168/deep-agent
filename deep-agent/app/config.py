"""应用配置（环境变量 + 默认值）"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """系统配置，所有字段都可以通过环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_prefix="DAGENT_",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 基础路径 ----
    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    # 前端构建产物目录（Vite build 输出到 frontend/dist）
    static_dir: Path = PROJECT_ROOT / "frontend" / "dist"
    db_path: Path = PROJECT_ROOT / "data" / "dagent.db"

    # ---- HTTP 服务 ----
    host: str = "127.0.0.1"
    port: int = 8765
    cors_origins: list[str] = ["*"]

    # ---- LLM ----
    # 可以是 ollama / openai / anthropic 等，详见 pydantic-ai 文档
    llm_model: str = "ollama:qwen2.5:7b"
    llm_api_key: str | None = None
    llm_base_url: str | None = None

    # ---- 流程引擎 ----
    heartbeat_interval_seconds: int = 10
    workflow_timeout_seconds: int = 600
    max_retries: int = 2

    # ---- 队列 ----
    queue_max_size: int = 1024

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
