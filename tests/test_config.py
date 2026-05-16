"""Тесты ConfigManager."""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from core.config import ConfigManager, _deep_merge


def test_deep_merge_nested():
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 99}, "e": 5}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": {"c": 99, "d": 3}, "e": 5}


def test_deep_merge_list_replaced():
    base = {"pairs": ["BTCUSDT", "ETHUSDT"]}
    override = {"pairs": ["SOLUSDT"]}
    result = _deep_merge(base, override)
    assert result["pairs"] == ["SOLUSDT"]


def test_config_load_basic(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "settings.yaml").write_text(yaml.dump({
        "app": {"name": "test", "log_level": "DEBUG"},
        "trading": {"min_spread_bps": 10},
    }))
    cfg = ConfigManager(config_dir=cfg_dir).load(env_file=None)
    assert cfg.get("app", "name") == "test"
    assert cfg.get("trading", "min_spread_bps") == 10


def test_config_get_default(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "settings.yaml").write_text(yaml.dump({"app": {}}))
    cfg = ConfigManager(config_dir=cfg_dir).load(env_file=None)
    assert cfg.get("missing", "key", default="fallback") == "fallback"
    assert cfg.get("missing", "key") is None


def test_config_require_raises(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "settings.yaml").write_text(yaml.dump({"app": {}}))
    cfg = ConfigManager(config_dir=cfg_dir).load(env_file=None)
    with pytest.raises(KeyError, match="Обязательный параметр"):
        cfg.require("nonexistent", "key")


def test_config_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "testnet")
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "settings.yaml").write_text(yaml.dump({"trading": {"min_spread_bps": 10}}))
    (cfg_dir / "testnet.yaml").write_text(yaml.dump({"trading": {"min_spread_bps": 5}}))
    cfg = ConfigManager(config_dir=cfg_dir).load(env_file=None)
    assert cfg.get("trading", "min_spread_bps") == 5


def test_config_env_var_substitution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DB_URL", "postgresql://localhost/test")
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "settings.yaml").write_text(yaml.dump({"storage": {"url": "${DB_URL}"}}))
    cfg = ConfigManager(config_dir=cfg_dir).load(env_file=None)
    assert cfg.get("storage", "url") == "postgresql://localhost/test"


def test_config_missing_base_file(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        ConfigManager(config_dir=cfg_dir).load(env_file=None)
