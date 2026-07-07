"""Repository behaviour: upsert idempotency, dedup, watermarks (T017–T021, T039)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from tgexport.storage.models import (
    RawAttachmentRef,
    RawDialog,
    RawEdit,
    RawMessage,
    RawReaction,
    RawSender,
)
from tgexport.storage.repos.attachments import (
    get_file_path_by_sha256,
    get_pending_attachments,
    resolve_attachment,
    upsert_attachment_ref,
)
from tgexport.storage.repos.chats import get_all_chats, get_chat, upsert_chat
from tgexport.storage.repos.messages import (
    get_message_count,
    get_messages,
    upsert_message,
)
from tgexport.storage.repos.sync_state import get_sync_state, update_watermark
from tgexport.storage.repos.users import upsert_user

DIALOG = RawDialog(id=100, type="private", title="Alice", username="alice", member_count=None)
SENDER = RawSender(id=1, username="alice", first_name="Alice", last_name=None)


def _msg(msg_id: int = 1, **overrides: object) -> RawMessage:
    defaults: dict[str, object] = {
        "id": msg_id,
        "chat_id": 100,
        "sender": SENDER,
        "date": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        "text": f"hello {msg_id}",
    }
    defaults.update(overrides)
    return RawMessage(**defaults)  # type: ignore[arg-type]


# ── chats ────────────────────────────────────────────────────────────────────


async def test_upsert_chat_creates_and_updates(db: aiosqlite.Connection) -> None:
    await upsert_chat(db, DIALOG)
    chat = await get_chat(db, 100)
    assert chat is not None and chat.title == "Alice"

    renamed = RawDialog(
        id=100, type="private", title="Alice B.", username="alice", member_count=None
    )
    await upsert_chat(db, renamed)
    chats = await get_all_chats(db)
    assert len(chats) == 1
    assert chats[0].title == "Alice B."


# ── users ────────────────────────────────────────────────────────────────────


async def test_upsert_user_idempotent_and_null_fields(db: aiosqlite.Connection) -> None:
    await upsert_user(db, SENDER)
    await upsert_user(db, SENDER)
    anonymous = RawSender(id=2, username=None, first_name=None, last_name=None)
    await upsert_user(db, anonymous)
    cursor = await db.execute("SELECT COUNT(*) FROM users")
    row = await cursor.fetchone()
    assert row is not None and row[0] == 2


# ── messages / edits / reactions ─────────────────────────────────────────────


async def test_upsert_message_with_edits_and_reactions(db: aiosqlite.Connection) -> None:
    await upsert_chat(db, DIALOG)
    msg = _msg(
        1,
        edits=(RawEdit(datetime(2026, 7, 1, 13, 0, tzinfo=UTC), "hello v2"),),
        reactions=(RawReaction("👍", 3),),
    )
    await upsert_message(db, msg)
    stored = await get_messages(db, 100)
    assert len(stored) == 1
    assert len(stored[0].edits) == 1
    assert stored[0].reactions[0].emoji == "👍"


async def test_repeated_upsert_no_duplicates(db: aiosqlite.Connection) -> None:
    """T039: same RawMessage twice → one message row, one edit, one reaction row."""
    await upsert_chat(db, DIALOG)
    msg = _msg(
        1,
        edits=(RawEdit(datetime(2026, 7, 1, 13, 0, tzinfo=UTC), "v2"),),
        reactions=(RawReaction("🔥", 1),),
    )
    await upsert_message(db, msg)
    await upsert_message(db, msg)
    assert await get_message_count(db, 100) == 1
    cursor = await db.execute("SELECT COUNT(*) FROM message_edits")
    row = await cursor.fetchone()
    assert row is not None and row[0] == 1
    cursor = await db.execute("SELECT COUNT(*) FROM reactions")
    row = await cursor.fetchone()
    assert row is not None and row[0] == 1


async def test_reaction_count_updates_on_refetch(db: aiosqlite.Connection) -> None:
    await upsert_chat(db, DIALOG)
    await upsert_message(db, _msg(1, reactions=(RawReaction("👍", 1),)))
    await upsert_message(db, _msg(1, reactions=(RawReaction("👍", 5),)))
    stored = await get_messages(db, 100)
    assert stored[0].reactions[0].count == 5


# ── attachments / files dedup ────────────────────────────────────────────────


async def test_attachment_pending_then_resolved(db: aiosqlite.Connection) -> None:
    await upsert_chat(db, DIALOG)
    await upsert_message(db, _msg(1))
    ref = RawAttachmentRef(tg_file_id="f1", category="photo")
    await upsert_attachment_ref(db, 1, 100, ref)

    pending = await get_pending_attachments(db, 100)
    assert len(pending) == 1 and pending[0].tg_file_id == "f1"

    await resolve_attachment(db, "f1", "abc123", Path("media/100/ab/c123_x.jpg"), "image/jpeg", 42)
    assert await get_pending_attachments(db, 100) == []
    assert await get_file_path_by_sha256(db, "abc123") == "media/100/ab/c123_x.jpg"


async def test_two_messages_share_one_file(db: aiosqlite.Connection) -> None:
    """Dedup: same SHA-256 from two messages → single files row."""
    await upsert_chat(db, DIALOG)
    await upsert_message(db, _msg(1))
    await upsert_message(db, _msg(2))
    await upsert_attachment_ref(db, 1, 100, RawAttachmentRef("f1", "photo"))
    await upsert_attachment_ref(db, 2, 100, RawAttachmentRef("f2", "photo"))
    await resolve_attachment(db, "f1", "samehash", Path("media/100/sa/mehash_x.jpg"), None, None)
    await resolve_attachment(db, "f2", "samehash", Path("media/100/sa/mehash_x.jpg"), None, None)
    cursor = await db.execute("SELECT COUNT(*) FROM files")
    row = await cursor.fetchone()
    assert row is not None and row[0] == 1
    cursor = await db.execute("SELECT COUNT(*) FROM attachments WHERE sha256 = 'samehash'")
    row = await cursor.fetchone()
    assert row is not None and row[0] == 2


# ── sync_state ───────────────────────────────────────────────────────────────


async def test_sync_state_lifecycle(db: aiosqlite.Connection) -> None:
    await upsert_chat(db, DIALOG)
    assert await get_sync_state(db, 100) is None

    await update_watermark(
        db, 100, last_message_id=50, oldest_message_id=10, history_complete=False
    )
    state = await get_sync_state(db, 100)
    assert state is not None
    assert state.last_message_id == 50 and not state.history_complete

    await update_watermark(db, 100, last_message_id=80, oldest_message_id=1, history_complete=True)
    state = await get_sync_state(db, 100)
    assert state is not None
    assert state.history_complete and state.last_message_id == 80
