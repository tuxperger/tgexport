"""Message repository: atomic upsert of message + edits + reactions; paginated reads."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from tgexport.storage.models import (
    Attachment,
    EditRecord,
    Message,
    RawMessage,
    Reaction,
)
from tgexport.storage.repos.users import upsert_user
from tgexport.storage.upsert import upsert


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def upsert_message(conn: aiosqlite.Connection, msg: RawMessage) -> None:
    """Idempotently store a message with its sender, edit history, and reactions."""
    if msg.sender is not None:
        await upsert_user(conn, msg.sender)

    await upsert(
        conn,
        "messages",
        {
            "id": msg.id,
            "chat_id": msg.chat_id,
            "sender_id": msg.sender.id if msg.sender else None,
            "sender_name": msg.sender.display_name if msg.sender else None,
            "date": _iso(msg.date),
            "text": msg.text,
            "reply_to_msg_id": msg.reply_to_msg_id,
            "fwd_from_chat_id": msg.fwd_from_chat_id,
            "fwd_from_msg_id": msg.fwd_from_msg_id,
            "fwd_from_name": msg.fwd_from_name,
            "service_type": msg.service_type,
            "is_deleted": int(msg.is_deleted),
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        conflict_cols=("id", "chat_id"),
    )

    for edit in msg.edits:
        await conn.execute(
            "INSERT OR IGNORE INTO message_edits (message_id, chat_id, edited_at, text) "
            "VALUES (?, ?, ?, ?)",
            (msg.id, msg.chat_id, _iso(edit.edited_at), edit.text),
        )

    for reaction in msg.reactions:
        await upsert(
            conn,
            "reactions",
            {
                "message_id": msg.id,
                "chat_id": msg.chat_id,
                "emoji": reaction.emoji,
                "count": reaction.count,
            },
            conflict_cols=("message_id", "chat_id", "emoji"),
        )

    await conn.commit()


async def _load_related(
    conn: aiosqlite.Connection, chat_id: int, message_ids: list[int]
) -> tuple[dict[int, list[EditRecord]], dict[int, list[Reaction]], dict[int, list[Attachment]]]:
    if not message_ids:
        return {}, {}, {}
    placeholders = ", ".join("?" for _ in message_ids)
    params = (chat_id, *message_ids)

    edits: dict[int, list[EditRecord]] = {}
    cursor = await conn.execute(
        f"SELECT message_id, chat_id, edited_at, text FROM message_edits "
        f"WHERE chat_id = ? AND message_id IN ({placeholders}) ORDER BY edited_at",
        params,
    )
    for row in await cursor.fetchall():
        edits.setdefault(row["message_id"], []).append(
            EditRecord(row["message_id"], row["chat_id"], row["edited_at"], row["text"])
        )

    reactions: dict[int, list[Reaction]] = {}
    cursor = await conn.execute(
        f"SELECT message_id, chat_id, emoji, count FROM reactions "
        f"WHERE chat_id = ? AND message_id IN ({placeholders}) ORDER BY count DESC",
        params,
    )
    for row in await cursor.fetchall():
        reactions.setdefault(row["message_id"], []).append(
            Reaction(row["message_id"], row["chat_id"], row["emoji"], row["count"])
        )

    attachments: dict[int, list[Attachment]] = {}
    cursor = await conn.execute(
        f"SELECT a.message_id, a.chat_id, a.tg_file_id, a.sha256, a.category, f.local_path "
        f"FROM attachments a LEFT JOIN files f ON f.sha256 = a.sha256 "
        f"WHERE a.chat_id = ? AND a.message_id IN ({placeholders})",
        params,
    )
    for row in await cursor.fetchall():
        attachments.setdefault(row["message_id"], []).append(
            Attachment(
                message_id=row["message_id"],
                chat_id=row["chat_id"],
                tg_file_id=row["tg_file_id"],
                sha256=row["sha256"],
                category=row["category"],
                local_path=row["local_path"],
            )
        )
    return edits, reactions, attachments


async def get_messages(
    conn: aiosqlite.Connection,
    chat_id: int,
    offset: int = 0,
    limit: int = 500,
) -> list[Message]:
    """Messages in chronological order with edits, reactions, and attachments attached."""
    cursor = await conn.execute(
        "SELECT * FROM messages WHERE chat_id = ? ORDER BY id LIMIT ? OFFSET ?",
        (chat_id, limit, offset),
    )
    rows = await cursor.fetchall()
    ids = [row["id"] for row in rows]
    edits, reactions, attachments = await _load_related(conn, chat_id, ids)

    return [
        Message(
            id=row["id"],
            chat_id=row["chat_id"],
            sender_id=row["sender_id"],
            sender_name=row["sender_name"],
            date=row["date"],
            text=row["text"],
            reply_to_msg_id=row["reply_to_msg_id"],
            fwd_from_chat_id=row["fwd_from_chat_id"],
            fwd_from_msg_id=row["fwd_from_msg_id"],
            fwd_from_name=row["fwd_from_name"],
            service_type=row["service_type"],
            is_deleted=bool(row["is_deleted"]),
            edits=tuple(edits.get(row["id"], ())),
            reactions=tuple(reactions.get(row["id"], ())),
            attachments=tuple(attachments.get(row["id"], ())),
        )
        for row in rows
    ]


async def get_messages_by_ids(
    conn: aiosqlite.Connection, chat_id: int, message_ids: list[int]
) -> dict[int, Message]:
    """Batch-load specific messages (used by render for reply quotes)."""
    if not message_ids:
        return {}
    placeholders = ", ".join("?" for _ in message_ids)
    cursor = await conn.execute(
        f"SELECT * FROM messages WHERE chat_id = ? AND id IN ({placeholders})",
        (chat_id, *message_ids),
    )
    result: dict[int, Message] = {}
    for row in await cursor.fetchall():
        result[row["id"]] = Message(
            id=row["id"],
            chat_id=row["chat_id"],
            sender_id=row["sender_id"],
            sender_name=row["sender_name"],
            date=row["date"],
            text=row["text"],
            reply_to_msg_id=row["reply_to_msg_id"],
            fwd_from_chat_id=row["fwd_from_chat_id"],
            fwd_from_msg_id=row["fwd_from_msg_id"],
            fwd_from_name=row["fwd_from_name"],
            service_type=row["service_type"],
            is_deleted=bool(row["is_deleted"]),
        )
    return result


async def get_message_count(conn: aiosqlite.Connection, chat_id: int) -> int:
    cursor = await conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,))
    row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


async def get_last_message_date(conn: aiosqlite.Connection, chat_id: int) -> str | None:
    cursor = await conn.execute("SELECT MAX(date) FROM messages WHERE chat_id = ?", (chat_id,))
    row = await cursor.fetchone()
    return row[0] if row is not None else None
