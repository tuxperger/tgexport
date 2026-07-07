"""Renderer output structure and content (T030)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from tgexport.render.renderer import render_all
from tgexport.storage.models import (
    RawAttachmentRef,
    RawDialog,
    RawEdit,
    RawMessage,
    RawReaction,
    RawSender,
)
from tgexport.storage.repos.attachments import resolve_attachment, upsert_attachment_ref
from tgexport.storage.repos.chats import upsert_chat
from tgexport.storage.repos.messages import upsert_message

SENDER = RawSender(id=1, username="alice", first_name="Alice", last_name="W")


@pytest.fixture
async def populated_db(db: aiosqlite.Connection) -> aiosqlite.Connection:
    await upsert_chat(db, RawDialog(100, "private", "Test Chat", "test", None))
    base = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

    await upsert_message(
        db,
        RawMessage(
            id=1,
            chat_id=100,
            sender=SENDER,
            date=base,
            text="first message",
            reactions=(RawReaction("👍", 2),),
        ),
    )
    await upsert_message(
        db,
        RawMessage(
            id=2,
            chat_id=100,
            sender=SENDER,
            date=base,
            text="a reply",
            reply_to_msg_id=1,
            edits=(RawEdit(base, "a reply (edited)"),),
        ),
    )
    await upsert_message(
        db,
        RawMessage(
            id=3,
            chat_id=100,
            sender=SENDER,
            date=base,
            text="forwarded thing",
            fwd_from_name="Bob Source",
            attachments=(RawAttachmentRef("f1", "photo"),),
        ),
    )
    await upsert_attachment_ref(db, 3, 100, RawAttachmentRef("f1", "photo"))
    await resolve_attachment(
        db, "f1", "aabbcc", Path("media/100/aa/bbcc_pic.jpg"), "image/jpeg", 10
    )
    return db


async def test_render_all_creates_expected_files(
    populated_db: aiosqlite.Connection, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    media = tmp_path / "data" / "media"
    await render_all(populated_db, output, media)

    index = output / "index.html"
    page = output / "100" / "page_001.html"
    assert index.exists()
    assert page.exists()

    index_html = index.read_text()
    assert "Test Chat" in index_html
    assert "3 messages" in index_html
    assert "100/page_001.html" in index_html


async def test_chat_page_contents(populated_db: aiosqlite.Connection, tmp_path: Path) -> None:
    output = tmp_path / "output"
    await render_all(populated_db, output, tmp_path / "data" / "media")
    html = (output / "100" / "page_001.html").read_text()

    assert "Alice W" in html
    assert "first message" in html
    assert "👍" in html  # reaction
    assert "reply-quote" in html  # reply block present
    assert "forwarded from Bob Source" in html
    assert "✎ edited" in html  # edit marker
    assert "bbcc_pic.jpg" in html  # resolved attachment path
    assert 'id="msg-1"' in html  # anchors for reply links


async def test_reply_to_missing_message_shows_placeholder(
    db: aiosqlite.Connection, tmp_path: Path
) -> None:
    await upsert_chat(db, RawDialog(200, "group", "G", None, 5))
    await upsert_message(
        db,
        RawMessage(
            id=1,
            chat_id=200,
            sender=SENDER,
            date=datetime(2026, 7, 1, tzinfo=UTC),
            text="orphan reply",
            reply_to_msg_id=999,
        ),
    )
    output = tmp_path / "out"
    await render_all(db, output, tmp_path / "media")
    html = (output / "200" / "page_001.html").read_text()
    assert "(message unavailable)" in html


async def test_pagination_splits_pages(db: aiosqlite.Connection, tmp_path: Path) -> None:
    await upsert_chat(db, RawDialog(300, "channel", "C", None, None))
    for i in range(1, 8):
        await upsert_message(
            db,
            RawMessage(
                id=i,
                chat_id=300,
                sender=SENDER,
                date=datetime(2026, 7, 1, tzinfo=UTC),
                text=f"m{i}",
            ),
        )
    output = tmp_path / "out"
    await render_all(db, output, tmp_path / "media", page_size=3)
    assert (output / "300" / "page_001.html").exists()
    assert (output / "300" / "page_002.html").exists()
    assert (output / "300" / "page_003.html").exists()
    assert not (output / "300" / "page_004.html").exists()
    page2 = (output / "300" / "page_002.html").read_text()
    assert "page 2 of 3" in page2
    assert "page_001.html" in page2 and "page_003.html" in page2
