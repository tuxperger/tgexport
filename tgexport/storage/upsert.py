"""Shared idempotent write helper used by all repositories."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiosqlite


async def upsert(
    conn: aiosqlite.Connection,
    table: str,
    data: Mapping[str, Any],
    conflict_cols: tuple[str, ...],
) -> None:
    """INSERT ... ON CONFLICT(conflict_cols) DO UPDATE SET <non-key columns>.

    If every column is part of the conflict key, falls back to DO NOTHING.
    """
    cols = list(data.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    update_cols = [c for c in cols if c not in conflict_cols]
    if update_cols:
        set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
        conflict_action = f"DO UPDATE SET {set_clause}"
    else:
        conflict_action = "DO NOTHING"
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT({', '.join(conflict_cols)}) {conflict_action}"
    )
    await conn.execute(sql, tuple(data.values()))
