"""Renderer output structure and content (T030)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


def _load(path: Path) -> Any:  # noqa: ANN401
    return json.loads(path.read_text(encoding="utf-8"))


async def test_render_all_creates_expected_files(
    populated_db: aiosqlite.Connection, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    media = tmp_path / "data" / "media"
    await render_all(populated_db, output, media)

    index = output / "index.html"
    assert index.exists()
    assert (output / "100" / "index.html").exists()
    assert sorted(p.name for p in (output / "100" / "data").iterdir()) == [
        "chunk_0000.json",
        "meta.json",
        "search.json",
    ]
    assert not list((output / "100").glob("page_*.html"))

    index_html = index.read_text()
    assert "Test Chat" in index_html
    assert "3 messages" in index_html
    assert "100/index.html" in index_html
    assert _load(output / "chats.json") == [{"id": 100, "title": "Test Chat"}]


async def test_chat_viewer_page_has_no_inline_messages(
    populated_db: aiosqlite.Connection, tmp_path: Path
) -> None:
    output = tmp_path / "output"
    await render_all(populated_db, output, tmp_path / "data" / "media")
    html = (output / "100" / "index.html").read_text()
    assert "Test Chat" in html
    assert "first message" not in html  # messages come from JSON, not the HTML
    assert "data/" in html


async def test_chunk_contents(populated_db: aiosqlite.Connection, tmp_path: Path) -> None:
    output = tmp_path / "output"
    await render_all(populated_db, output, tmp_path / "data" / "media")
    msgs = {m["id"]: m for m in _load(output / "100" / "data" / "chunk_0000.json")}

    assert msgs[1]["sender"] == "Alice W"
    assert msgs[1]["text"] == "first message"
    assert msgs[1]["reactions"] == [["👍", 2]]
    assert msgs[2]["reply"] == {"id": 1, "sender": "Alice W", "text": "first message"}
    assert msgs[2]["edits"][0][1] == "a reply (edited)"
    assert msgs[3]["fwd_from"] == "Bob Source"
    assert msgs[3]["attachments"] == [{"category": "photo", "path": "media/100/aa/bbcc_pic.jpg"}]
    assert "reply" not in msgs[1] and "deleted" not in msgs[1]

    meta = _load(output / "100" / "data" / "meta.json")
    assert meta["chat"]["title"] == "Test Chat"
    assert meta["message_count"] == 3
    assert meta["media_prefix"].startswith("../")
    assert meta["chunks"] == [
        {
            "file": "chunk_0000.json",
            "count": 3,
            "first_id": 1,
            "last_id": 3,
            "first_date": "2026-07-01T12:00:00Z",
            "last_date": "2026-07-01T12:00:00Z",
        }
    ]


async def test_reply_to_missing_message_marked(db: aiosqlite.Connection, tmp_path: Path) -> None:
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
    (msg,) = _load(output / "200" / "data" / "chunk_0000.json")
    assert msg["reply"] == {"id": 999, "missing": True}


async def _seed_channel(db: aiosqlite.Connection, n: int) -> None:
    await upsert_chat(db, RawDialog(300, "channel", "C", None, None))
    for i in range(1, n + 1):
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


async def test_chunking_and_search_index(db: aiosqlite.Connection, tmp_path: Path) -> None:
    await _seed_channel(db, 7)
    output = tmp_path / "out"
    await render_all(db, output, tmp_path / "media", chunk_size=3)
    data = output / "300" / "data"
    assert [len(_load(data / f"chunk_{i:04d}.json")) for i in range(3)] == [3, 3, 1]
    assert not (data / "chunk_0003.json").exists()

    meta = _load(data / "meta.json")
    assert [(c["first_id"], c["last_id"]) for c in meta["chunks"]] == [(1, 3), (4, 6), (7, 7)]

    # [id, chunk, sender, date, text]
    chunks = {record[0]: record[1] for record in _load(data / "search.json")}
    assert chunks == {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1, 7: 2}


async def test_rerender_removes_stale_output(db: aiosqlite.Connection, tmp_path: Path) -> None:
    await _seed_channel(db, 7)
    output = tmp_path / "out"
    legacy = output / "300" / "page_001.html"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old")
    await render_all(db, output, tmp_path / "media", chunk_size=3)
    await render_all(db, output, tmp_path / "media", chunk_size=10)
    assert not legacy.exists()
    assert sorted(p.name for p in (output / "300" / "data").glob("chunk_*")) == ["chunk_0000.json"]
