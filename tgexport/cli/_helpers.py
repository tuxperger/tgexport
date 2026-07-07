"""Shared CLI helpers: config resolution with directory overrides."""

from __future__ import annotations

import dataclasses
import logging
import sys
from pathlib import Path

import click

from tgexport.core.config import Config, ConfigError, load_config

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_TELEGRAM_ERROR = 1
EXIT_CONFIG_ERROR = 2
EXIT_NOT_IMPLEMENTED = 3


def resolve_config(ctx: click.Context, *, require_api: bool = True) -> Config:
    """Load Config applying --env / --data-dir / --output-dir overrides.

    Exits with EXIT_CONFIG_ERROR on missing required settings (unless
    require_api is False, e.g. for the offline `render` command).
    """
    env_file: Path | None = ctx.obj.get("env_file")
    data_dir: Path | None = ctx.obj.get("data_dir")
    output_dir: Path | None = ctx.obj.get("output_dir")
    try:
        cfg = load_config(env_file)
    except ConfigError as exc:
        if require_api:
            logger.error(str(exc))
            sys.exit(EXIT_CONFIG_ERROR)
        # Offline commands don't need api_id/api_hash; build a config with dummies.
        import os

        cfg = Config(
            api_id=0,
            api_hash="",
            session_path=Path(os.environ.get("TG_SESSION_PATH", "data/session.session")),
            db_path=Path(os.environ.get("TG_DB_PATH", "data/archive.db")),
            media_dir=Path(os.environ.get("TG_MEDIA_DIR", "data/media")),
            output_dir=Path(os.environ.get("TG_OUTPUT_DIR", "output")),
        )
    if data_dir is not None:
        cfg = dataclasses.replace(
            cfg,
            session_path=data_dir / "session.session",
            db_path=data_dir / "archive.db",
            media_dir=data_dir / "media",
        )
    if output_dir is not None:
        cfg = dataclasses.replace(cfg, output_dir=output_dir)
    return cfg
