"""`tgexport chats` — list all dialogs with their IDs for use in chat filters."""

from __future__ import annotations

import asyncio
import logging
import sys

import click
from rich.table import Table

from tgexport.cli._console import console
from tgexport.cli._helpers import EXIT_TELEGRAM_ERROR, resolve_config
from tgexport.core.config import ChatFilter, Config, DialogType
from tgexport.fetch.client import TelegramSession
from tgexport.fetch.dialogs import iter_dialogs
from tgexport.fetch.rate_limiter import RateLimiter
from tgexport.storage.models import RawDialog

logger = logging.getLogger(__name__)


async def _fetch_dialogs(cfg: Config, types: tuple[DialogType, ...]) -> list[RawDialog]:
    chat_filter = ChatFilter(types=types) if types else None
    rate_limiter = RateLimiter()
    dialogs: list[RawDialog] = []
    async with TelegramSession(cfg) as client:
        async for dialog in iter_dialogs(client, rate_limiter, chat_filter):
            dialogs.append(dialog)
    return dialogs


@click.command()
@click.option(
    "--type",
    "types",
    multiple=True,
    type=click.Choice(["private", "group", "supergroup", "channel"]),
    help="Restrict to dialog type(s).",
)
@click.pass_context
def chats(ctx: click.Context, types: tuple[DialogType, ...]) -> None:
    """List all chats/groups/channels with their IDs.

    Use the IDs (or exact titles) in TG_INCLUDE_CHATS / TG_EXCLUDE_CHATS in
    your .env, or with `export --include/--exclude`, to choose what to save.
    """
    cfg = resolve_config(ctx)
    try:
        dialogs = asyncio.run(_fetch_dialogs(cfg, types))
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("Failed to list chats: %s", exc)
        sys.exit(EXIT_TELEGRAM_ERROR)

    table = Table(title=f"Dialogs ({len(dialogs)})")
    table.add_column("ID", justify="right", no_wrap=True)
    table.add_column("Title")
    table.add_column("Type")
    table.add_column("Username")
    table.add_column("Members", justify="right")
    for d in dialogs:
        table.add_row(
            str(d.id),
            d.title,
            d.type,
            f"@{d.username}" if d.username else "",
            str(d.member_count) if d.member_count is not None else "",
        )
    console.print(table)
    console.print(
        "[dim]Save selected chats: TG_INCLUDE_CHATS=<id1>,<id2> in .env "
        "or `tgexport export --include <id>`[/dim]"
    )
