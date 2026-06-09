"""运行时配置服务：从 DB 读取（UI 可改），并回退到 .env 默认值。"""
from __future__ import annotations

import os
from typing import Optional

from app.db import repository as repo
from app.db.session import session_scope
from app.logging import logger


# 配置项的 key 常量
K_LLM_PROVIDER = "llm.provider"  # ollama | openai | anthropic | deepseek | glm | openai_compat
K_LLM_MODEL = "llm.model"
K_LLM_BASE_URL = "llm.base_url"
K_LLM_API_KEY = "llm.api_key"
K_LLM_TEMPERATURE = "llm.temperature"


# 全部 keys（前端 GET 时也用这个清单来过滤）
SETTING_KEYS: list[str] = [
    K_LLM_PROVIDER,
    K_LLM_MODEL,
    K_LLM_BASE_URL,
    K_LLM_API_KEY,
    K_LLM_TEMPERATURE,
]


# 敏感 key（GET 时不返回明文）
SECRET_KEYS: set[str] = {K_LLM_API_KEY}


# DB 未配置时回退到环境变量 / 默认
_DEFAULTS = {
    K_LLM_PROVIDER: os.getenv("DAGENT_LLM_PROVIDER", "ollama"),
    K_LLM_MODEL: os.getenv("DAGENT_LLM_MODEL", "qwen2.5:7b"),
    K_LLM_BASE_URL: os.getenv("DAGENT_LLM_BASE_URL", "http://127.0.0.1:11434/v1"),
    K_LLM_API_KEY: os.getenv("DAGENT_LLM_API_KEY", ""),
    K_LLM_TEMPERATURE: os.getenv("DAGENT_LLM_TEMPERATURE", "0.2"),
}


def _normalize(value: Optional[str], default: str) -> str:
    if value is None or value == "":
        return default
    return value


async def get(key: str) -> str:
    """读单个配置：DB 优先，env 兜底。"""
    default = _DEFAULTS.get(key, "")
    async with session_scope() as session:
        v = await repo.get_setting(session, key)
    return _normalize(v, default)


async def get_all() -> dict[str, str]:
    """读全部配置。"""
    async with session_scope() as session:
        all_db = await repo.get_all_settings(session)
    out: dict[str, str] = {}
    for k in SETTING_KEYS:
        v = all_db.get(k)
        out[k] = _normalize(v, _DEFAULTS.get(k, ""))
    return out


async def get_all_public() -> dict[str, str]:
    """读全部配置（API key 脱敏）。"""
    out = await get_all()
    if K_LLM_API_KEY in out and out[K_LLM_API_KEY]:
        out[K_LLM_API_KEY] = "***" + out[K_LLM_API_KEY][-4:] if len(out[K_LLM_API_KEY]) > 4 else "***"
    return out


async def set_many(items: dict[str, Optional[str]]) -> None:
    """批量写入配置。空字符串视为清除（落回默认值）。"""
    cleaned: dict[str, Optional[str]] = {}
    for k, v in items.items():
        if k not in SETTING_KEYS:
            continue
        # 脱敏占位符 "***xxxx" 不写入
        if k in SECRET_KEYS and v and v.startswith("***"):
            continue
        # 空字符串 → 写 None（清除）
        cleaned[k] = v if v else None
    if not cleaned:
        return
    async with session_scope() as session:
        await repo.apply_settings_bulk(session, cleaned)
    logger.info("运行时配置已更新: {}", list(cleaned.keys()))


async def get_llm_config() -> dict[str, str]:
    """planner 用的便捷方法，一次性返回所有 LLM 配置。"""
    out = await get_all()
    return {
        "provider": out.get(K_LLM_PROVIDER, _DEFAULTS[K_LLM_PROVIDER]),
        "model": out.get(K_LLM_MODEL, _DEFAULTS[K_LLM_MODEL]),
        "base_url": out.get(K_LLM_BASE_URL, _DEFAULTS[K_LLM_BASE_URL]),
        "api_key": out.get(K_LLM_API_KEY, _DEFAULTS[K_LLM_API_KEY]),
        "temperature": out.get(K_LLM_TEMPERATURE, _DEFAULTS[K_LLM_TEMPERATURE]),
    }
