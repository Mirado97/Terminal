"""Политики перезапуска воркеров."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RestartPolicy(str, Enum):
    ALWAYS     = "always"       # перезапускать всегда (в т.ч. при штатном стопе)
    ON_FAILURE = "on_failure"   # только при ошибке
    NEVER      = "never"        # не перезапускать


@dataclass
class WorkerPolicy:
    restart: RestartPolicy = RestartPolicy.ON_FAILURE
    max_restarts: int = 10          # -1 = бесконечно
    base_backoff_s: float = 1.0     # начальная задержка перезапуска
    max_backoff_s: float = 60.0     # максимальная задержка
    heartbeat_timeout_s: float = 30.0  # таймаут heartbeat → воркер считается мёртвым

    def backoff_for(self, attempt: int) -> float:
        """Exponential backoff: base * 2^attempt, но не более max."""
        return min(self.base_backoff_s * (2 ** attempt), self.max_backoff_s)
