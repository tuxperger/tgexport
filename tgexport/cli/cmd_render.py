"""`tgexport render` — regenerate the HTML archive from the local DB (offline)."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click

from tgexport.cli._helpers import EXIT_TELEGRAM_ERROR, resolve_config
from tgexport.core.config import Config
from tgexport.render.renderer import render_all
from tgexport.storage.db import open_db

logger = logging.getLogger(__name__)


async def _run_render(cfg: Config, chat_ids: list[int] | None, page_size: int) -> None:
    conn = await open_db(cfg.db_path)
    try:
        await render_all(
            conn, cfg.output_dir, cfg.media_dir, page_size=page_size, chat_ids=chat_ids
        )
    finally:
        await conn.close()


@click.command()
@click.option("--include", multiple=True, help="Limit render to these chat IDs (repeatable).")
@click.option("--page-size", default=500, show_default=True, help="Messages per HTML page.")
@click.pass_context
def render(ctx: click.Context, include: tuple[str, ...], page_size: int) -> None:
    """Generate the static HTML archive from the local database. No network needed."""
    cfg = resolve_config(ctx, require_api=False)
    if not Path(cfg.db_path).exists():
        logger.error("Database not found at %s — run `tgexport export` first.", cfg.db_path)
        sys.exit(EXIT_TELEGRAM_ERROR)
    chat_ids = [int(x) for x in include] if include else None
    asyncio.run(_run_render(cfg, chat_ids, page_size))
    click.echo(f"Archive written to {cfg.output_dir}/index.html")
