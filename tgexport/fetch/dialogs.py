"""Dialog enumeration."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from telethon import TelegramClient
from telethon.tl.custom.dialog import Dialog

from tgexport.core.config import ChatFilter, DialogType
from tgexport.fetch.rate_limiter import RateLimiter
from tgexport.storage.models import RawDialog

logger = logging.getLogger(__name__)


def _dialog_type(dialog: Dialog) -> DialogType:
    if dialog.is_user:
        return "private"
    if dialog.is_channel:
        # Telethon marks both supergroups and broadcast channels as is_channel;
        # is_group distinguishes supergroups.
        return "supergroup" if dialog.is_group else "channel"
    return "group"


async def iter_dialogs(
    client: TelegramClient,
    rate_limiter: RateLimiter,
    chat_filter: ChatFilter | None = None,
) -> AsyncIterator[RawDialog]:
    """Yield all accessible dialogs, applying the optional filter."""
    await rate_limiter.acquire()
    async for dialog in client.iter_dialogs():
        dtype = _dialog_type(dialog)
        title = dialog.title or str(dialog.id)
        if chat_filter is not None and not chat_filter.matches(dialog.id, title, dtype):
            logger.debug("Skipping dialog %s (%s): filtered out", title, dialog.id)
            continue
        entity = dialog.entity
        logger.debug("Dialog %s (%s, %s): matched, syncing", title, dialog.id, dtype)
        yield RawDialog(
            id=dialog.id,
            type=dtype,
            title=title,
            username=getattr(entity, "username", None),
            member_count=getattr(entity, "participants_count", None),
        )
