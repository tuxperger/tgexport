"""Per-chat watermark repository."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from tgexport.storage.models import SyncState
from tgexport.storage.upsert import upsert


async def get_sync_state(conn: aiosqlite.Connection, chat_id: int) -> SyncState | None:
    cursor = await conn.execute("SELECT * FROM sync_state WHERE chat_id = ?", (chat_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return SyncState(
        chat_id=row["chat_id"],
        last_message_id=row["last_message_id"],
        oldest_message_id=row["oldest_message_id"],
        history_complete=bool(row["history_complete"]),
        updated_at=row["updated_at"],
    )


async def update_watermark(
    conn: aiosqlite.Connection,
    chat_id: int,
    last_message_id: int,
    oldest_message_id: int | None,
    history_complete: bool,
) -> None:
    await upsert(
        conn,
        "sync_state",
        {
            "chat_id": chat_id,
            "last_message_id": last_message_id,
            "oldest_message_id": oldest_message_id,
            "history_complete": int(history_complete),
            "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        conflict_cols=("chat_id",),
    )
    await conn.commit()
