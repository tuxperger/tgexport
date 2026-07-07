"""Static HTML generation from the local database. Zero network access."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
from jinja2 import Environment, PackageLoader, select_autoescape

from tgexport.render.filters import format_datetime, media_icon, truncate_text
from tgexport.storage.models import Chat
from tgexport.storage.repos.chats import get_all_chats, get_chat
from tgexport.storage.repos.messages import (
    get_last_message_date,
    get_message_count,
    get_messages,
    get_messages_by_ids,
)

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 500


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


def _media_prefix(output_dir: Path, media_dir: Path) -> str:
    """Relative path prefix from a chat page directory to the media root.

    Chat pages live at output/<chat_id>/page_NNN.html, so media referenced as
    media/<chat_id>/... needs the prefix computed from output/<chat_id>/.
    """
    rel = os.path.relpath(media_dir.parent, output_dir)
    return f"../{rel}/".replace("\\", "/")


async def render_chat(
    conn: aiosqlite.Connection,
    chat_id: int,
    output_dir: Path,
    media_dir: Path,
    page_size: int = DEFAULT_PAGE_SIZE,
    env: Environment | None = None,
) -> None:
    """Render (or re-render) all pages for a single chat."""
    if env is None:
        env = _environment()
    chat = await get_chat(conn, chat_id)
    if chat is None:
        logger.warning("render_chat: chat %d not found in DB", chat_id)
        return

    total = await get_message_count(conn, chat_id)
    total_pages = max(1, math.ceil(total / page_size))
    chat_dir = output_dir / str(chat_id)
    chat_dir.mkdir(parents=True, exist_ok=True)
    template = env.get_template("chat.html.j2")
    prefix = _media_prefix(output_dir, media_dir)

    for page in range(1, total_pages + 1):
        messages = await get_messages(conn, chat_id, offset=(page - 1) * page_size, limit=page_size)
        reply_ids = [m.reply_to_msg_id for m in messages if m.reply_to_msg_id is not None]
        reply_map = await get_messages_by_ids(conn, chat_id, reply_ids)
        html = template.render(
            chat=chat,
            messages=messages,
            reply_map=reply_map,
            page=page,
            total_pages=total_pages,
            media_prefix=prefix,
        )
        (chat_dir / f"page_{page:03d}.html").write_text(html, encoding="utf-8")
    logger.info("Rendered %s: %d page(s), %d message(s)", chat.title, total_pages, total)


async def render_all(
    conn: aiosqlite.Connection,
    output_dir: Path,
    media_dir: Path,
    page_size: int = DEFAULT_PAGE_SIZE,
    chat_ids: list[int] | None = None,
) -> None:
    """Generate index.html plus per-chat pages for all (or selected) chats."""
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
        await render_chat(conn, chat.id, output_dir, media_dir, page_size, env)

    entries.sort(key=lambda e: e.last_date or "", reverse=True)
    index_html = env.get_template("index.html.j2").render(chats=entries)
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")
    logger.info("Rendered index with %d chat(s) → %s", len(entries), output_dir / "index.html")
