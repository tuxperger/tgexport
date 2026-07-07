"""Connection pragmas and config validation."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from tgexport.core.config import ConfigError, load_config


async def test_foreign_keys_enabled(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA foreign_keys")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 1


async def test_journal_mode_set(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row is not None
    # In-memory databases report "memory"; file-backed ones report "wal".
    assert row[0] in ("wal", "memory")


def test_missing_credentials_raise_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    empty_env = tmp_path / ".env"
    empty_env.write_text("")
    with pytest.raises(ConfigError, match="TG_API_ID"):
        load_config(empty_env)


def test_non_integer_api_id_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TG_API_ID", "not-a-number")
    monkeypatch.setenv("TG_API_HASH", "abc")
    empty_env = tmp_path / ".env"
    empty_env.write_text("")
    with pytest.raises(ConfigError, match="integer"):
        load_config(empty_env)
