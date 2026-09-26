"""LLM dataset export: channel post selection, example formats, dedup and split."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from tgexport.render.dataset import DatasetOptions, export_dataset, make_example, select_posts
from tgexport.storage.models import Chat, Message, RawDialog, RawMessage
from tgexport.storage.repos.chats import upsert_chat
from tgexport.storage.repos.messages import upsert_message

BASE = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
CHANNEL = Chat(10, "channel", "Tech Notes", None, None, "")
LONG = "Сегодня разберём, почему **SQLite** отлично подходит для локальных архивов."


def _msg(i: int, text: str | None, fwd: str | None = None, service: str | None = None) -> Message:
    return Message(
        id=i,
        chat_id=10,
        sender_id=None,
        sender_name=None,
        date=(BASE + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        text=text,
        reply_to_msg_id=None,
        fwd_from_chat_id=None,
        fwd_from_msg_id=None,
        fwd_from_name=fwd,
        service_type=service,
        is_deleted=False,
    )


def test_select_posts_filters() -> None:
    msgs = [
        _msg(1, f"  {LONG}  "),
        _msg(2, None),  # album item without caption
        _msg(3, "🔥"),  # too short
        _msg(4, LONG + " (repost)", fwd="Other Channel"),
        _msg(5, LONG + " pinned", service="pin"),
        _msg(6, LONG + " 2"),
    ]
    assert select_posts(msgs, DatasetOptions()) == [LONG, LONG + " 2"]
    with_fwd = select_posts(msgs, DatasetOptions(include_forwards=True, min_chars=1))
    assert with_fwd == [LONG, "🔥", LONG + " (repost)", LONG + " 2"]


def test_chat_format() -> None:
    opts = DatasetOptions(system_prompt="Автор «{chat}»", user_prompt="Пост для {chat}")
    assert make_example(CHANNEL, LONG, opts) == {
        "messages": [
            {"role": "system", "content": "Автор «Tech Notes»"},
            {"role": "user", "content": "Пост для Tech Notes"},
            {"role": "assistant", "content": LONG},
        ]
    }
    assert make_example(CHANNEL, LONG, DatasetOptions(system_prompt=None)) == {
        "messages": [
            {"role": "user", "content": "Напиши новый пост для канала."},
            {"role": "assistant", "content": LONG},
        ]
    }


def test_text_format() -> None:
    assert make_example(CHANNEL, LONG, DatasetOptions(format="text")) == {"text": LONG}


async def test_export_channels_only_dedup_and_split(
    db: aiosqlite.Connection, tmp_path: Path
) -> None:
    await upsert_chat(db, RawDialog(10, "channel", "A", None, None))
    await upsert_chat(db, RawDialog(11, "channel", "B", None, None))
    await upsert_chat(db, RawDialog(12, "private", "Friend", None, None))
    posts = {10: [LONG + " a1", LONG + " a2", LONG], 11: [LONG + " b1", LONG], 12: [LONG + " p"]}
    for chat_id, texts in posts.items():
        for i, text in enumerate(texts, start=1):
            await upsert_message(
                db, RawMessage(i, chat_id, None, BASE + timedelta(minutes=i), text)
            )

    stats = await export_dataset(db, tmp_path, DatasetOptions(format="text", val_ratio=0.25))
    assert (stats.channels, stats.posts, stats.duplicates) == (2, 4, 1)
    assert (stats.train, stats.val) == (3, 1)
    written = {
        json.loads(line)["text"]
        for name in ("train.jsonl", "val.jsonl")
        for line in (tmp_path / name).read_text().splitlines()
    }
    assert written == {LONG + " a1", LONG + " a2", LONG, LONG + " b1"}
