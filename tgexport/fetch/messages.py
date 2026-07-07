"""Message history iteration with pagination and full metadata extraction."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC

from telethon import TelegramClient
from telethon.tl.custom.message import Message as TLMessage

from tgexport.fetch.rate_limiter import RateLimiter
from tgexport.storage.models import (
    AttachmentCategory,
    RawAttachmentRef,
    RawEdit,
    RawMessage,
    RawReaction,
    RawSender,
)

logger = logging.getLogger(__name__)


def _classify_media(message: TLMessage) -> RawAttachmentRef | None:
    """Map a Telethon message's media to one of the eight attachment categories."""
    if message.photo is not None:
        return RawAttachmentRef(
            tg_file_id=f"photo_{message.photo.id}",
            category="photo",
            filename=None,
            mime_type="image/jpeg",
        )
    doc = message.document
    if doc is None:
        return None

    category: AttachmentCategory
    if message.sticker is not None:
        category = "sticker"
    elif message.gif is not None:
        category = "gif"
    elif message.video_note is not None:
        category = "video_note"
    elif message.voice is not None:
        category = "voice"
    elif message.video is not None:
        category = "video"
    elif message.audio is not None:
        category = "audio"
    else:
        category = "document"

    filename = None
    for attr in doc.attributes:
        if hasattr(attr, "file_name"):
            filename = attr.file_name
            break
    return RawAttachmentRef(
        tg_file_id=f"doc_{doc.id}",
        category=category,
        filename=filename,
        mime_type=doc.mime_type,
    )


def _extract_sender(message: TLMessage) -> RawSender | None:
    sender = message.sender
    if sender is None:
        return None
    return RawSender(
        id=getattr(sender, "id", None),
        username=getattr(sender, "username", None),
        first_name=getattr(sender, "first_name", None) or getattr(sender, "title", None),
        last_name=getattr(sender, "last_name", None),
    )


def _extract_reactions(message: TLMessage) -> tuple[RawReaction, ...]:
    if message.reactions is None or message.reactions.results is None:
        return ()
    result = []
    for r in message.reactions.results:
        emoticon = getattr(r.reaction, "emoticon", None)
        if emoticon:
            result.append(RawReaction(emoji=emoticon, count=r.count))
    return tuple(result)


def _extract_forward(message: TLMessage) -> tuple[int | None, int | None, str | None]:
    fwd = message.forward
    if fwd is None:
        return None, None, None
    chat_id = fwd.chat_id if fwd.chat_id else None
    msg_id = fwd.channel_post if fwd.channel_post else None
    name = fwd.from_name
    if name is None and fwd.sender is not None:
        first = getattr(fwd.sender, "first_name", None) or getattr(fwd.sender, "title", "")
        last = getattr(fwd.sender, "last_name", None) or ""
        name = f"{first} {last}".strip() or None
    return chat_id, msg_id, name


def _to_raw_message(message: TLMessage, chat_id: int) -> RawMessage:
    fwd_chat, fwd_msg, fwd_name = _extract_forward(message)
    edits: tuple[RawEdit, ...] = ()
    if message.edit_date is not None:
        edits = (RawEdit(edited_at=message.edit_date.astimezone(UTC), text=message.text),)
    attachment = _classify_media(message)
    service_type = None
    if message.action is not None:
        service_type = type(message.action).__name__.removeprefix("MessageAction")

    return RawMessage(
        id=message.id,
        chat_id=chat_id,
        sender=_extract_sender(message),
        date=message.date.astimezone(UTC),
        text=message.text or None,
        reply_to_msg_id=message.reply_to_msg_id,
        fwd_from_chat_id=fwd_chat,
        fwd_from_msg_id=fwd_msg,
        fwd_from_name=fwd_name,
        service_type=service_type,
        is_deleted=False,
        edits=edits,
        reactions=_extract_reactions(message),
        attachments=(attachment,) if attachment else (),
    )


async def iter_messages(
    client: TelegramClient,
    rate_limiter: RateLimiter,
    chat_id: int,
    min_id: int = 0,
    max_id: int = 0,
    batch_size: int = 100,
) -> AsyncIterator[RawMessage]:
    """Yield messages for a chat, newest first.

    - min_id > 0: incremental forward sync — only messages newer than min_id.
    - max_id > 0: backward-scan resumption — only messages older than max_id.
    - neither: full history scan from newest to oldest.

    The rate limiter is acquired once per underlying page request
    (Telethon fetches in batches of `batch_size`).
    """
    count_in_page = 0
    total = 0
    await rate_limiter.acquire()
    async for message in client.iter_messages(chat_id, min_id=min_id, max_id=max_id, limit=None):
        yield _to_raw_message(message, chat_id)
        count_in_page += 1
        total += 1
        if count_in_page >= batch_size:
            count_in_page = 0
            logger.debug(
                "chat %d: fetched %d messages, at message id %d", chat_id, total, message.id
            )
            await rate_limiter.acquire()
    logger.debug("chat %d: iteration finished, %d messages fetched", chat_id, total)
