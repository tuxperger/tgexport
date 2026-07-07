"""`tgexport watch` — reserved for the future live mode. Exits 3 (not implemented)."""

from __future__ import annotations

import asyncio
import sys

import click

from tgexport.cli._helpers import EXIT_NOT_IMPLEMENTED, resolve_config
from tgexport.live.daemon import run_daemon
from tgexport.storage.db import open_db


@click.command()
@click.pass_context
def watch(ctx: click.Context) -> None:
    """Watch for real-time updates (live mode). Not implemented yet."""
    # The stub must not demand API credentials or create files on disk just to
    # report "not implemented"; an in-memory DB satisfies run_daemon's contract.
    cfg = resolve_config(ctx, require_api=False)

    async def _run() -> None:
        conn = await open_db(":memory:")
        try:
            await run_daemon(cfg, conn, cfg.output_dir)
        finally:
            await conn.close()

    try:
        asyncio.run(_run())
    except NotImplementedError as exc:
        click.echo(str(exc), err=True)
        sys.exit(EXIT_NOT_IMPLEMENTED)
