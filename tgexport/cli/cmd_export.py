"""`tgexport export` — fetch history + attachments into the DB, then render.

This module orchestrates fetch → storage. Attachment deduplication lives here
(not in fetch): fetch returns (tmp_path, sha256); we check the files table,
discard duplicates, move new files into the sharded media layout, and record
the result via storage.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path

import aiosqlite
import click
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from telethon import TelegramClient

from tgexport.cli._console import console
from tgexport.cli._helpers import EXIT_TELEGRAM_ERROR, resolve_config
from tgexport.core.config import ChatFilter, Config, DialogType
from tgexport.fetch.client import TelegramSession
from tgexport.fetch.dialogs import iter_dialogs
from tgexport.fetch.media import download_attachment, sharded_media_path
from tgexport.fetch.messages import iter_messages
from tgexport.fetch.rate_limiter import RateLimiter
from tgexport.render.renderer import render_all
from tgexport.storage.db import open_db
from tgexport.storage.models import RawDialog
from tgexport.storage.repos.attachments import (
    get_file_path_by_sha256,
    get_pending_attachments,
    resolve_attachment,
    upsert_attachment_ref,
)
from tgexport.storage.repos.chats import upsert_chat
from tgexport.storage.repos.messages import upsert_message
from tgexport.storage.repos.sync_state import get_sync_state, update_watermark

logger = logging.getLogger(__name__)


def _parse_filter(
    include: tuple[str, ...], exclude: tuple[str, ...], types: tuple[str, ...]
) -> ChatFilter | None:
    if not include and not exclude and not types:
        return None
    return ChatFilter(
        include=tuple(include),
        exclude=tuple(exclude),
        types=tuple(types) if types else None,  # type: ignore[arg-type]
    )


async def _sync_dialog_messages(
    client: TelegramClient,
    conn: aiosqlite.Connection,
    rate_limiter: RateLimiter,
    dialog: RawDialog,
    batch_size: int,
) -> int:
    """Fetch messages for one dialog using the dual-cursor watermark.

    Returns the number of newly stored messages.
    """
    state = await get_sync_state(conn, dialog.id)
    if state is not None and state.history_complete:
        min_id, max_id = state.last_message_id, 0
        mode = "incremental (only messages newer than last_message_id)"
    elif state is not None and state.oldest_message_id is not None:
        # Resume interrupted initial backward scan.
        min_id, max_id = 0, state.oldest_message_id
        mode = "resume backward scan"
    else:
        min_id, max_id = 0, 0
        mode = "full history scan"
    logger.debug(
        "[%s] sync mode: %s (min_id=%d, max_id=%d, state=%s)",
        dialog.title,
        mode,
        min_id,
        max_id,
        state,
    )

    last_id = state.last_message_id if state else 0
    oldest_id = state.oldest_message_id if state else None
    stored = 0
    batch_count = 0

    async for msg in iter_messages(
        client, rate_limiter, dialog.id, min_id=min_id, max_id=max_id, batch_size=batch_size
    ):
        await upsert_message(conn, msg)
        for ref in msg.attachments:
            await upsert_attachment_ref(conn, msg.id, msg.chat_id, ref)
        stored += 1
        batch_count += 1
        last_id = max(last_id, msg.id)
        oldest_id = msg.id if oldest_id is None else min(oldest_id, msg.id)
        if batch_count >= batch_size:
            batch_count = 0
            await update_watermark(conn, dialog.id, last_id, oldest_id, history_complete=False)
            logger.debug(
                "[%s] watermark committed: %d messages stored so far (last_id=%d, oldest_id=%s)",
                dialog.title,
                stored,
                last_id,
                oldest_id,
            )

    # Iteration exhausted → the backward scan (if any) reached the beginning.
    await update_watermark(conn, dialog.id, last_id, oldest_id, history_complete=True)
    logger.debug("[%s] sync done: %d new messages stored", dialog.title, stored)
    return stored


async def _download_pending_attachments(
    client: TelegramClient,
    conn: aiosqlite.Connection,
    rate_limiter: RateLimiter,
    cfg: Config,
    chat_id: int,
    progress: Progress,
) -> tuple[int, int]:
    """Download attachments with sha256 IS NULL. Returns (downloaded, failed)."""
    pending = await get_pending_attachments(conn, chat_id)
    logger.debug("[chat %d] pending attachments: %d", chat_id, len(pending))
    if not pending:
        return 0, 0
    task = progress.add_task(f"  media for {chat_id}", total=len(pending))
    tmp_dir = cfg.media_dir / ".tmp"
    downloaded = failed = 0

    for idx, item in enumerate(pending, 1):
        label = f"  [{idx}/{len(pending)}] {item.category} for msg {item.message_id}"
        progress.update(task, description=f"{label}: queued")

        def _on_status(phase: str, label: str = label) -> None:
            progress.update(task, description=f"{label}: {phase}")

        last_reported = [-1]

        def _on_bytes(
            current: int, total: int, label: str = label, last: list[int] = last_reported
        ) -> None:
            # Throttle redraws to ~1 per MiB, but always show the first and last chunk.
            if 0 <= last[0] and current - last[0] < 2**20 and current != total:
                return
            last[0] = current
            percent = f" ({current / total:.0%})" if total else ""
            progress.update(
                task,
                description=f"{label}: {current / 2**20:.1f}/{total / 2**20:.1f} MB{percent}",
            )

        try:
            result = await download_attachment(
                client,
                rate_limiter,
                item.chat_id,
                item.message_id,
                tmp_dir,
                progress_callback=_on_bytes,
                status_callback=_on_status,
            )
        except Exception as exc:
            logger.warning("Download failed chat=%d msg=%d: %s", item.chat_id, item.message_id, exc)
            failed += 1
            progress.advance(task)
            continue
        if result is None:
            failed += 1
            progress.advance(task)
            continue

        tmp_path, sha256 = result
        existing = await get_file_path_by_sha256(conn, sha256)
        if existing is not None:
            # Duplicate content: reuse the already-stored file.
            logger.debug(
                "[chat %d] msg %d: duplicate content, reusing %s",
                item.chat_id,
                item.message_id,
                existing,
            )
            tmp_path.unlink(missing_ok=True)
            final_rel = Path(existing)
        else:
            file_size = tmp_path.stat().st_size
            final_abs = sharded_media_path(cfg.media_dir, item.chat_id, sha256, tmp_path.name)
            final_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_path), final_abs)
            final_rel = final_abs.relative_to(cfg.media_dir.parent)
            logger.info(
                "Saved %s for msg %d → %s (%.1f MB)",
                item.category,
                item.message_id,
                final_rel,
                file_size / 2**20,
            )
        await resolve_attachment(
            conn,
            item.tg_file_id,
            sha256,
            final_rel,
            mime_type=None,
            file_size=None,
        )
        downloaded += 1
        progress.advance(task)

    progress.remove_task(task)
    return downloaded, failed


async def _process_dialog(
    client: TelegramClient,
    conn: aiosqlite.Connection,
    rate_limiter: RateLimiter,
    cfg: Config,
    dialog: RawDialog,
    batch_size: int,
    no_media: bool,
    progress: Progress,
    summary: list[tuple[str, int, int, int]],
) -> None:
    """Sync one dialog's messages and media; errors are logged, not raised,
    so a failing chat doesn't abort the other concurrent workers."""
    task = progress.add_task(f"{dialog.title}", total=None)
    try:
        await upsert_chat(conn, dialog)
        stored = await _sync_dialog_messages(client, conn, rate_limiter, dialog, batch_size)
        downloaded = failed = 0
        if not no_media:
            downloaded, failed = await _download_pending_attachments(
                client, conn, rate_limiter, cfg, dialog.id, progress
            )
        summary.append((dialog.title, stored, downloaded, failed))
        logger.info(
            "%s: %d new messages, %d media downloaded, %d failed",
            dialog.title,
            stored,
            downloaded,
            failed,
        )
    except Exception:
        logger.exception("%s: sync failed — continuing with other chats", dialog.title)
        summary.append((dialog.title, -1, -1, -1))
    finally:
        progress.remove_task(task)


async def _run_export(
    cfg: Config,
    chat_filter: ChatFilter | None,
    no_media: bool,
    no_render: bool,
    batch_size: int,
    concurrency: int,
) -> None:
    rate_limiter = RateLimiter()
    logger.info(
        "Writing to db=%s media=%s (filter: %s)",
        cfg.db_path.resolve(),
        cfg.media_dir.resolve(),
        chat_filter if chat_filter is not None else "none",
    )
    conn = await open_db(cfg.db_path)
    summary: list[tuple[str, int, int, int]] = []

    try:
        async with TelegramSession(cfg) as client:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                semaphore = asyncio.Semaphore(max(1, concurrency))

                async def bounded(dialog: RawDialog) -> None:
                    async with semaphore:
                        await _process_dialog(
                            client,
                            conn,
                            rate_limiter,
                            cfg,
                            dialog,
                            batch_size,
                            no_media,
                            progress,
                            summary,
                        )

                workers = [
                    asyncio.create_task(bounded(dialog))
                    async for dialog in iter_dialogs(client, rate_limiter, chat_filter)
                ]
                if workers:
                    await asyncio.gather(*workers)

        if not no_render:
            await render_all(conn, cfg.output_dir, cfg.media_dir)
    finally:
        await conn.close()

    table = Table(title="Export summary")
    table.add_column("Chat")
    table.add_column("New messages", justify="right")
    table.add_column("Media downloaded", justify="right")
    table.add_column("Media failed", justify="right")
    for title, stored, downloaded, failed in summary:
        table.add_row(title, str(stored), str(downloaded), str(failed))
    console.print(table)


@click.command()
@click.option("--include", multiple=True, help="Whitelist: chat ID or exact title (repeatable).")
@click.option("--exclude", multiple=True, help="Blacklist: chat ID or exact title (repeatable).")
@click.option(
    "--type",
    "types",
    multiple=True,
    type=click.Choice(["private", "group", "supergroup", "channel"]),
    help="Restrict to dialog type(s).",
)
@click.option("--no-media", is_flag=True, help="Skip attachment downloads.")
@click.option("--no-render", is_flag=True, help="Skip HTML generation after fetch.")
@click.option("--batch-size", default=200, show_default=True, help="Messages per watermark commit.")
@click.option(
    "--concurrency",
    default=4,
    show_default=True,
    help="Chats synced in parallel. Higher values increase FloodWait risk.",
)
@click.pass_context
def export(
    ctx: click.Context,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    types: tuple[DialogType, ...],
    no_media: bool,
    no_render: bool,
    batch_size: int,
    concurrency: int,
) -> None:
    """Fetch (or incrementally update) history and attachments, then render HTML."""
    cfg = resolve_config(ctx)
    # CLI flags take precedence over the TG_INCLUDE_CHATS/… settings from .env.
    chat_filter = _parse_filter(include, exclude, types) or cfg.chat_filter
    try:
        asyncio.run(_run_export(cfg, chat_filter, no_media, no_render, batch_size, concurrency))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted — progress saved; rerun to resume.[/yellow]")
        sys.exit(EXIT_TELEGRAM_ERROR)
