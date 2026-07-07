"""Attachment repository: pending refs and content-hash deduplicated files."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from tgexport.storage.models import PendingAttachment, RawAttachmentRef
from tgexport.storage.upsert import upsert


async def upsert_attachment_ref(
    conn: aiosqlite.Connection,
    msg_id: int,
    chat_id: int,
    ref: RawAttachmentRef,
) -> None:
    """Record an attachment reference with download pending (sha256 = NULL).

    An existing resolved row is left untouched: ON CONFLICT only bumps the
    category, never resets sha256.
    """
    await conn.execute(
        "INSERT INTO attachments (message_id, chat_id, tg_file_id, sha256, category) "
        "VALUES (?, ?, ?, NULL, ?) "
        "ON CONFLICT(message_id, chat_id, tg_file_id) DO NOTHING",
        (msg_id, chat_id, ref.tg_file_id, ref.category),
    )
    await conn.commit()


async def resolve_attachment(
    conn: aiosqlite.Connection,
    tg_file_id: str,
    sha256: str,
    local_path: Path,
    mime_type: str | None,
    file_size: int | None,
) -> None:
    """Mark an attachment as downloaded: upsert the file record and link it."""
    await upsert(
        conn,
        "files",
        {
            "sha256": sha256,
            "local_path": str(local_path),
            "mime_type": mime_type,
            "file_size": file_size,
        },
        conflict_cols=("sha256",),
    )
    await conn.execute(
        "UPDATE attachments SET sha256 = ? WHERE tg_file_id = ?",
        (sha256, tg_file_id),
    )
    await conn.commit()


async def get_file_path_by_sha256(conn: aiosqlite.Connection, sha256: str) -> str | None:
    cursor = await conn.execute("SELECT local_path FROM files WHERE sha256 = ?", (sha256,))
    row = await cursor.fetchone()
    return row[0] if row is not None else None


async def get_pending_attachments(
    conn: aiosqlite.Connection, chat_id: int | None = None
) -> list[PendingAttachment]:
    """Attachment rows whose download has not completed yet."""
    if chat_id is None:
        cursor = await conn.execute(
            "SELECT message_id, chat_id, tg_file_id, category FROM attachments WHERE sha256 IS NULL"
        )
    else:
        cursor = await conn.execute(
            "SELECT message_id, chat_id, tg_file_id, category FROM attachments "
            "WHERE sha256 IS NULL AND chat_id = ?",
            (chat_id,),
        )
    return [
        PendingAttachment(
            message_id=row["message_id"],
            chat_id=row["chat_id"],
            tg_file_id=row["tg_file_id"],
            category=row["category"],
        )
        for row in await cursor.fetchall()
    ]
