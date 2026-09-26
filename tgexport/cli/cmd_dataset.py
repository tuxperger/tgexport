"""`tgexport dataset` — build an LLM fine-tuning dataset of channel posts (offline)."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click

from tgexport.cli._helpers import EXIT_TELEGRAM_ERROR, resolve_config
from tgexport.core.config import DIALOG_TYPES, Config, DialogType
from tgexport.render.dataset import (
    DATASET_FORMATS,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_USER_PROMPT,
    DatasetFormat,
    DatasetOptions,
    DatasetStats,
    export_dataset,
)
from tgexport.storage.db import open_db

logger = logging.getLogger(__name__)


async def _run_dataset(cfg: Config, out_dir: Path, opts: DatasetOptions) -> DatasetStats:
    conn = await open_db(cfg.db_path)
    try:
        return await export_dataset(conn, out_dir, opts)
    finally:
        await conn.close()


@click.command()
@click.option(
    "--out",
    "out_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Output directory [default: <data dir>/dataset].",
)
@click.option("--include", multiple=True, type=int, help="Only these chat IDs (repeatable).")
@click.option(
    "--type",
    "types",
    multiple=True,
    type=click.Choice(DIALOG_TYPES),
    help="Dialog types to take posts from [default: channel].",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(DATASET_FORMATS),
    default="chat",
    show_default=True,
    help="chat: system/user/assistant messages; text: raw posts for a base model.",
)
@click.option(
    "--system",
    "system_prompt",
    default=DEFAULT_SYSTEM_PROMPT,
    show_default=True,
    help='System prompt ("{chat}" → channel title); empty string to omit.',
)
@click.option(
    "--prompt",
    "user_prompt",
    default=DEFAULT_USER_PROMPT,
    show_default=True,
    help='User instruction preceding each post ("{chat}" → channel title).',
)
@click.option("--min-chars", default=40, show_default=True, help="Skip shorter posts.")
@click.option("--include-forwards", is_flag=True, help="Keep reposts from other channels.")
@click.option("--val-ratio", default=0.05, show_default=True, help="Share of posts for val.")
@click.option("--seed", default=42, show_default=True, help="Shuffle seed for the split.")
@click.pass_context
def dataset(
    ctx: click.Context,
    out_dir: Path | None,
    include: tuple[int, ...],
    types: tuple[DialogType, ...],
    fmt: DatasetFormat,
    system_prompt: str,
    user_prompt: str,
    min_chars: int,
    include_forwards: bool,
    val_ratio: float,
    seed: int,
) -> None:
    """Export channel posts as train/val JSONL for style fine-tuning."""
    cfg = resolve_config(ctx, require_api=False)
    if not Path(cfg.db_path).exists():
        logger.error("Database not found at %s — run `tgexport export` first.", cfg.db_path)
        sys.exit(EXIT_TELEGRAM_ERROR)
    if out_dir is None:
        out_dir = cfg.db_path.parent / "dataset"
    opts = DatasetOptions(
        types=types or DatasetOptions.types,
        chat_ids=include or None,
        format=fmt,
        system_prompt=system_prompt or None,
        user_prompt=user_prompt,
        min_chars=min_chars,
        include_forwards=include_forwards,
        val_ratio=val_ratio,
        seed=seed,
    )
    stats = asyncio.run(_run_dataset(cfg, out_dir, opts))
    click.echo(
        f"{stats.posts} post(s) from {stats.channels} channel(s) "
        f"({stats.duplicates} duplicate(s) skipped): "
        f"{stats.train} → {out_dir}/train.jsonl, {stats.val} → {out_dir}/val.jsonl"
    )
