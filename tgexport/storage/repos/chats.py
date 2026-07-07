"""Chat repository."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from tgexport.storage.models import Chat, RawDialog
from tgexport.storage.upsert import upsert


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def upsert_chat(conn: aiosqlite.Connection, dialog: RawDialog) -> None:
    await upsert(
        conn,
        "chats",
        {
            "id": dialog.id,
            "type": dialog.type,
            "title": dialog.title,
            "username": dialog.username,
            "member_count": dialog.member_count,
            "fetched_at": _now(),
        },
        conflict_cols=("id",),
    )
    await conn.commit()


def _row_to_chat(row: aiosqlite.Row) -> Chat:
    return Chat(
        id=row["id"],
        type=row["type"],
        title=row["title"],
        username=row["username"],
        member_count=row["member_count"],
        fetched_at=row["fetched_at"],
    )


async def get_all_chats(conn: aiosqlite.Connection) -> list[Chat]:
    cursor = await conn.execute("SELECT * FROM chats ORDER BY title")
    return [_row_to_chat(row) for row in await cursor.fetchall()]


async def get_chat(conn: aiosqlite.Connection, chat_id: int) -> Chat | None:
    cursor = await conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,))
    row = await cursor.fetchone()
    return _row_to_chat(row) if row is not None else None
