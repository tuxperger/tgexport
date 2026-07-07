"""Migration runner behaviour."""

from __future__ import annotations

import aiosqlite

from tgexport.storage.db import open_db, run_migrations

EXPECTED_TABLES = {
    "chats",
    "users",
    "messages",
    "message_edits",
    "files",
    "attachments",
    "reactions",
    "sync_state",
}


async def _table_names(conn: aiosqlite.Connection) -> set[str]:
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in await cursor.fetchall()}


async def test_initial_migration_creates_all_tables(db: aiosqlite.Connection) -> None:
    tables = await _table_names(db)
    assert EXPECTED_TABLES <= tables


async def test_rerunning_migrations_is_idempotent(db: aiosqlite.Connection) -> None:
    await run_migrations(db)
    await run_migrations(db)
    cursor = await db.execute("SELECT COUNT(*) FROM _schema_migrations")
    row = await cursor.fetchone()
    assert row is not None
    count = row[0]
    cursor = await db.execute("SELECT COUNT(DISTINCT id) FROM _schema_migrations")
    row = await cursor.fetchone()
    assert row is not None
    assert count == row[0], "duplicate migration entries found"


async def test_open_db_memory_succeeds() -> None:
    conn = await open_db(":memory:")
    try:
        tables = await _table_names(conn)
        assert "chats" in tables
    finally:
        await conn.close()
