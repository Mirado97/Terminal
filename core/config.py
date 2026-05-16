"""Управление конфигурацией: YAML + переменные окружения."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value: Any) -> Any:
    """Рекурсивно заменяет ${VAR} на значения из окружения."""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            name = m.group(1)
            v = os.environ.get(name)
            if v is None:
                raise ValueError(f"Переменная окружения не задана: {name}")
            return v
        return _ENV_VAR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def _deep_merge(base: dict, override: dict) -> dict:
    """Глубокое слияние двух словарей (override побеждает)."""
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


class ConfigManager:
    """
    Загружает конфиг из YAML с поддержкой:
    - базового config/settings.yaml,
    - оверрайда по APP_ENV (testnet.yaml, production.yaml),
    - подстановки ${VAR} из переменных окружения.
    """

    def __init__(self, config_dir: Path | str = "config") -> None:
        self._config_dir = Path(config_dir)
        self._data: dict[str, Any] = {}

    def load(self, env_file: str | Path | None = ".env") -> "ConfigManager":
        if env_file is not None and Path(env_file).exists():
            load_dotenv(env_file)

        base_path = self._config_dir / "settings.yaml"
        if not base_path.exists():
            raise FileNotFoundError(f"Базовый конфиг не найден: {base_path}")

        with base_path.open("r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}

        app_env = os.environ.get("APP_ENV", "")
        if app_env:
            override_path = self._config_dir / f"{app_env}.yaml"
            if override_path.exists():
                with override_path.open("r", encoding="utf-8") as f:
                    override = yaml.safe_load(f) or {}
                self._data = _deep_merge(self._data, override)

        # Разрешаем ${VAR} только там, где нет необязательных переменных.
        # storage.* содержат ${POSTGRES_URL} и ${REDIS_URL} — пропускаем если не заданы.
        self._data = self._resolve_safe(self._data)
        return self

    def _resolve_safe(self, value: Any) -> Any:
        """Разрешает env vars, пропуская незаданные (вместо ValueError — оставляет placeholder)."""
        if isinstance(value, str):
            def _replace(m: re.Match) -> str:
                name = m.group(1)
                return os.environ.get(name, m.group(0))  # оставляем ${VAR} если не задано
            return _ENV_VAR_RE.sub(_replace, value)
        if isinstance(value, dict):
            return {k: self._resolve_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_safe(v) for v in value]
        return value

    def get(self, *keys: str, default: Any = None) -> Any:
        """Получить значение по цепочке ключей: get('exchanges', 'bybit', 'rest_url')."""
        node = self._data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def require(self, *keys: str) -> Any:
        """Обязательное значение — KeyError если отсутствует."""
        value = self.get(*keys)
        if value is None:
            raise KeyError(f"Обязательный параметр конфига отсутствует: {'.'.join(keys)}")
        return value

    @property
    def raw(self) -> dict[str, Any]:
        return self._data
