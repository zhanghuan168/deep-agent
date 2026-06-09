"""专家池（注册与获取）。"""
from __future__ import annotations

from typing import Iterable

from app.agents.base import BaseExpertAgent
from app.logging import logger


class ExpertPool:
    """简单的字典式专家池。"""

    def __init__(self) -> None:
        self._pool: dict[str, BaseExpertAgent] = {}

    def register(self, agent: BaseExpertAgent) -> None:
        if agent.expert_type in self._pool:
            logger.warning("专家 {} 已存在，将被覆盖", agent.expert_type)
        self._pool[agent.expert_type] = agent
        logger.info("注册专家: {}", agent.expert_type)

    def register_many(self, agents: Iterable[BaseExpertAgent]) -> None:
        for a in agents:
            self.register(a)

    def get(self, expert_type: str) -> BaseExpertAgent:
        if expert_type not in self._pool:
            raise KeyError(f"未注册的专家类型: {expert_type}")
        return self._pool[expert_type]

    def __contains__(self, expert_type: str) -> bool:
        return expert_type in self._pool

    def types(self) -> list[str]:
        return list(self._pool.keys())


# 全局单例
expert_pool = ExpertPool()
