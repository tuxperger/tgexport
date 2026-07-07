"""Unit tests for configuration loading, including TG_API_CREDENTIALS=desktop."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from tgexport.core.config import ConfigError, load_config

# Explicit nonexistent .env: keeps load_dotenv from picking up the project's real one.
_EMPTY_ENV = Path("nonexistent.env")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Run from an empty directory so load_dotenv() cannot pick up a real .env.
    monkeypatch.chdir(tmp_path)
    for name in (
        "TG_API_ID",
        "TG_API_HASH",
        "TG_API_CREDENTIALS",
        "TG_INCLUDE_CHATS",
        "TG_EXCLUDE_CHATS",
        "TG_CHAT_TYPES",
    ):
        monkeypatch.delenv(name, raising=False)


def _install_fake_opentele(monkeypatch: pytest.MonkeyPatch) -> None:
    api_module = types.ModuleType("opentele.api")
    api_module.API = types.SimpleNamespace(  # type: ignore[attr-defined]
        TelegramDesktop=types.SimpleNamespace(api_id=2040, api_hash="deadbeef")
    )
    opentele = types.ModuleType("opentele")
    opentele.api = api_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "opentele", opentele)
    monkeypatch.setitem(sys.modules, "opentele.api", api_module)


def test_custom_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_API_ID", "123")
    monkeypatch.setenv("TG_API_HASH", "abc")
    cfg = load_config(_EMPTY_ENV)
    assert cfg.api_id == 123
    assert cfg.api_hash == "abc"


def test_missing_credentials_mentions_desktop_alternative() -> None:
    with pytest.raises(ConfigError, match="TG_API_CREDENTIALS=desktop"):
        load_config(_EMPTY_ENV)


def test_desktop_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_opentele(monkeypatch)
    monkeypatch.setenv("TG_API_CREDENTIALS", "desktop")
    cfg = load_config(_EMPTY_ENV)
    assert cfg.api_id == 2040
    assert cfg.api_hash == "deadbeef"


def test_desktop_credentials_without_opentele(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without opentele the bundled Telegram Desktop constants are used, so
    # servers running an already-imported session don't need the tdata extra.
    monkeypatch.setitem(sys.modules, "opentele", None)  # force ImportError
    monkeypatch.setenv("TG_API_CREDENTIALS", "desktop")
    cfg = load_config(_EMPTY_ENV)
    assert cfg.api_id == 2040
    assert cfg.api_hash == "b18441a1ff607e10a989891a5462e627"


def test_invalid_credentials_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_API_CREDENTIALS", "bogus")
    with pytest.raises(ConfigError, match="custom.*desktop"):
        load_config(_EMPTY_ENV)


def _set_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_API_ID", "123")
    monkeypatch.setenv("TG_API_HASH", "abc")


def test_no_chat_filter_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_api(monkeypatch)
    assert load_config(_EMPTY_ENV).chat_filter is None


def test_chat_filter_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_api(monkeypatch)
    monkeypatch.setenv("TG_INCLUDE_CHATS", "123, -100456 ,Saved Messages")
    monkeypatch.setenv("TG_EXCLUDE_CHATS", "-100789")
    monkeypatch.setenv("TG_CHAT_TYPES", "private,supergroup")
    chat_filter = load_config(_EMPTY_ENV).chat_filter
    assert chat_filter is not None
    assert chat_filter.include == ("123", "-100456", "Saved Messages")
    assert chat_filter.exclude == ("-100789",)
    assert chat_filter.types == ("private", "supergroup")
    assert chat_filter.matches(123, "whatever", "private")
    assert not chat_filter.matches(123, "whatever", "channel")
    assert not chat_filter.matches(-100789, "Saved Messages", "private")


def test_chat_filter_invalid_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_api(monkeypatch)
    monkeypatch.setenv("TG_CHAT_TYPES", "gruop")
    with pytest.raises(ConfigError, match="gruop"):
        load_config(_EMPTY_ENV)
