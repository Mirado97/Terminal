"""Базовый класс репозитория."""
from __future__ import annotations

from storage.db import DatabasePool


class BaseRepository:
    def __init__(self, pool: DatabasePool) -> None:
        self._pool = pool
