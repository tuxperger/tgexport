"""Database connection and migration runner."""

from __future__ import annotations

import os
import stat
from importlib import resources
from pathlib import Path

import aiosqlite

_MIGRATIONS_PACKAGE = "tgexport.storage.migrations"


async def open_db(path: Path | str) -> aiosqlite.Connection:
    """Open the archive database, applying pragmas and any pending migrations.

    For file-backed databases the file is created with mode 600 and its parent
    directory with mode 700 (constitution: local-first and privacy).
    """
    is_memory = str(path) == ":memory:"
    if not is_memory:
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        os.chmod(parent, stat.S_IRWXU)

    conn = await aiosqlite.connect(str(path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA foreign_keys = ON")
    await run_migrations(conn)

    if not is_memory:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return conn


async def run_migrations(conn: aiosqlite.Connection) -> None:
    """Apply unapplied numbered migrations, tracking them in _schema_migrations.

    The meta-table is created here (not in a migration file) to avoid a
    chicken-and-egg problem on first open. Re-running is a no-op.
    """
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS _schema_migrations ("
        " id TEXT PRIMARY KEY,"
        " applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))"
    )
    cursor = await conn.execute("SELECT id FROM _schema_migrations")
    applied = {row[0] for row in await cursor.fetchall()}

    migration_files = sorted(
        entry.name
        for entry in resources.files(_MIGRATIONS_PACKAGE).iterdir()
        if entry.name.endswith(".sql")
    )
    for name in migration_files:
        if name in applied:
            continue
        sql = resources.files(_MIGRATIONS_PACKAGE).joinpath(name).read_text(encoding="utf-8")
        await conn.executescript(sql)
        await conn.execute("INSERT INTO _schema_migrations (id) VALUES (?)", (name,))
    await conn.commit()
