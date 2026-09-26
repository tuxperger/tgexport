"""Static archive generation from the local database. Zero network access.

Layout of the generated archive:

    output/index.html                  chat list + global search
    output/chats.json                  [{id, title}] for the global search
    output/<chat_id>/index.html        single viewer page for the chat
    output/<chat_id>/data/meta.json    chat info + chunk directory
    output/<chat_id>/data/chunk_NNNN.json   messages, chronological, fixed size
    output/<chat_id>/data/search.json  [id, chunk, sender, date, text] tuples

The viewer pages load the JSON with fetch(), which browsers refuse over
file:// — open the archive through `tgexport serve`.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
from jinja2 import Environment, PackageLoader, select_autoescape

from tgexport.render.filters import format_datetime, media_icon, truncate_text
from tgexport.storage.models import Chat, Message
from tgexport.storage.repos.chats import get_all_chats, get_chat
from tgexport.storage.repos.messages import (
    get_last_message_date,
    get_message_count,
    get_messages,
    get_messages_by_ids,
)

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 1000
DATA_DIR = "data"
REPLY_SNIPPET = 200


@dataclass(frozen=True)
class ChatIndexEntry:
    chat: Chat
    message_count: int
    last_date: str | None


def _environment() -> Environment:
    env = Environment(
        loader=PackageLoader("tgexport.render", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["format_datetime"] = format_datetime
    env.filters["media_icon"] = media_icon
    env.filters["truncate_text"] = truncate_text
    return env


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def _media_prefix(output_dir: Path, media_dir: Path) -> str:
    """Relative path prefix from a chat page directory to the media root.

    The chat viewer lives at output/<chat_id>/index.html, so media referenced
    as media/<chat_id>/... needs the prefix computed from output/<chat_id>/.
    """
    rel = os.path.relpath(media_dir.parent, output_dir)
    return f"../{rel}/".replace("\\", "/")


def _message_json(msg: Message, reply_map: dict[int, Message]) -> dict[str, Any]:
    """Serialize a message for the viewer; empty/false fields are omitted."""
    out: dict[str, Any] = {"id": msg.id, "date": msg.date}
    if msg.sender_name:
        out["sender"] = msg.sender_name
    if msg.sender_id is not None:
        out["sender_id"] = msg.sender_id
    if msg.text:
        out["text"] = msg.text
    if msg.service_type:
        out["service"] = msg.service_type
    if msg.is_deleted:
        out["deleted"] = True
    if msg.fwd_from_name:
        out["fwd_from"] = msg.fwd_from_name
    if msg.reply_to_msg_id is not None:
        quoted = reply_map.get(msg.reply_to_msg_id)
        reply: dict[str, Any] = {"id": msg.reply_to_msg_id}
        if quoted is not None:
            reply["sender"] = quoted.sender_name or "Unknown"
            reply["text"] = truncate_text(quoted.text, REPLY_SNIPPET)
        else:
            reply["missing"] = True
        out["reply"] = reply
    if msg.attachments:
        out["attachments"] = [
            {"category": a.category, "path": a.local_path} for a in msg.attachments
        ]
    if msg.reactions:
        out["reactions"] = [[r.emoji, r.count] for r in msg.reactions]
    if msg.edits:
        out["edits"] = [[e.edited_at, e.text] for e in msg.edits]
    return out


def _clean_chat_dir(chat_dir: Path) -> None:
    """Remove output from previous renders (incl. the legacy paginated layout)."""
    shutil.rmtree(chat_dir / DATA_DIR, ignore_errors=True)
    for legacy in (*chat_dir.glob("page_*.html"), chat_dir / "search.js"):
        legacy.unlink(missing_ok=True)


async def render_chat(
    conn: aiosqlite.Connection,
    chat_id: int,
    output_dir: Path,
    media_dir: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    env: Environment | None = None,
) -> None:
    """Render (or re-render) the viewer page and JSON data for a single chat."""
    if env is None:
        env = _environment()
    chat = await get_chat(conn, chat_id)
    if chat is None:
        logger.warning("render_chat: chat %d not found in DB", chat_id)
        return

    total = await get_message_count(conn, chat_id)
    total_chunks = max(1, math.ceil(total / chunk_size))
    chat_dir = output_dir / str(chat_id)
    _clean_chat_dir(chat_dir)
    data_dir = chat_dir / DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    chunks: list[dict[str, Any]] = []
    # [id, chunk, sender, date, text] tuples for the client-side search index.
    search_records: list[list[Any]] = []
    for idx in range(total_chunks):
        messages = await get_messages(conn, chat_id, offset=idx * chunk_size, limit=chunk_size)
        reply_ids = [m.reply_to_msg_id for m in messages if m.reply_to_msg_id is not None]
        reply_map = await get_messages_by_ids(conn, chat_id, reply_ids)
        name = f"chunk_{idx:04d}.json"
        _write_json(data_dir / name, [_message_json(m, reply_map) for m in messages])
        chunks.append(
            {
                "file": name,
                "count": len(messages),
                "first_id": messages[0].id if messages else None,
                "last_id": messages[-1].id if messages else None,
                "first_date": messages[0].date if messages else None,
                "last_date": messages[-1].date if messages else None,
            }
        )
        search_records.extend(
            [m.id, idx, m.sender_name or "", m.date, m.text]
            for m in messages
            if m.text and not m.service_type
        )

    _write_json(
        data_dir / "meta.json",
        {
            "chat": {
                "id": chat.id,
                "type": chat.type,
                "title": chat.title,
                "username": chat.username,
                "member_count": chat.member_count,
            },
            "message_count": total,
            "chunk_size": chunk_size,
            "media_prefix": _media_prefix(output_dir, media_dir),
            "chunks": chunks,
        },
    )
    _write_json(data_dir / "search.json", search_records)
    html = env.get_template("chat.html.j2").render(chat=chat)
    (chat_dir / "index.html").write_text(html, encoding="utf-8")
    logger.info(
        "Rendered %s: %d message(s) in %d chunk(s), %d in search index",
        chat.title,
        total,
        total_chunks,
        len(search_records),
    )


async def render_all(
    conn: aiosqlite.Connection,
    output_dir: Path,
    media_dir: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chat_ids: list[int] | None = None,
) -> None:
    """Generate index.html plus per-chat viewers for all (or selected) chats."""
    env = _environment()
    output_dir.mkdir(parents=True, exist_ok=True)

    chats = await get_all_chats(conn)
    if chat_ids is not None:
        wanted = set(chat_ids)
        chats = [c for c in chats if c.id in wanted]

    entries: list[ChatIndexEntry] = []
    for chat in chats:
        count = await get_message_count(conn, chat.id)
        last_date = await get_last_message_date(conn, chat.id)
        entries.append(ChatIndexEntry(chat=chat, message_count=count, last_date=last_date))
        await render_chat(conn, chat.id, output_dir, media_dir, chunk_size, env)

    entries.sort(key=lambda e: e.last_date or "", reverse=True)
    _write_json(
        output_dir / "chats.json",
        [{"id": e.chat.id, "title": e.chat.title} for e in entries if e.message_count],
    )
    (output_dir / "chats.js").unlink(missing_ok=True)  # legacy JSONP index
    index_html = env.get_template("index.html.j2").render(chats=entries)
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")
    logger.info("Rendered index with %d chat(s) → %s", len(entries), output_dir / "index.html")
