"""Управление API-ключами бирж из .env файла."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class ExchangeCredentials:
    api_key: str
    api_secret: str
    passphrase: str = ""  # нужен для OKX, Bitget


class SecretsManager:
    """
    Читает API credentials из переменных окружения.

    Формат для testnet (APP_ENV=testnet):
        {EXCHANGE}_API_KEY_TESTNET
        {EXCHANGE}_API_SECRET_TESTNET
        {EXCHANGE}_PASSPHRASE_TESTNET  (опционально)

    Формат для prod:
        {EXCHANGE}_API_KEY
        {EXCHANGE}_API_SECRET
        {EXCHANGE}_PASSPHRASE  (опционально)
    """

    def __init__(self, env_file: str | Path = ".env") -> None:
        env_path = Path(env_file)
        if env_path.exists():
            load_dotenv(env_path)
        self._testnet = os.environ.get("APP_ENV", "") == "testnet"

    def get_credentials(self, exchange: str) -> ExchangeCredentials:
        prefix = exchange.upper()
        suffix = "_TESTNET" if self._testnet else ""

        api_key = self._require(f"{prefix}_API_KEY{suffix}")
        api_secret = self._require(f"{prefix}_API_SECRET{suffix}")
        passphrase = os.environ.get(f"{prefix}_PASSPHRASE{suffix}", "")

        return ExchangeCredentials(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
        )

    def validate_all(self, exchanges: list[str]) -> dict[str, bool]:
        """Проверить наличие credentials для списка бирж."""
        result: dict[str, bool] = {}
        for exchange in exchanges:
            try:
                self.get_credentials(exchange)
                result[exchange] = True
            except EnvironmentError:
                result[exchange] = False
        return result

    def _require(self, var: str) -> str:
        value = os.environ.get(var)
        if not value:
            raise EnvironmentError(
                f"Переменная окружения не задана: {var}\n"
                f"Скопируйте .env.example в .env и заполните значения."
            )
        return value
