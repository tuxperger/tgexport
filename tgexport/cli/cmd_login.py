"""`tgexport login` — interactive authentication, saving the session file."""

from __future__ import annotations

import asyncio
import dataclasses
import getpass
import logging
import sys
from pathlib import Path

import click

from tgexport.cli._helpers import EXIT_TELEGRAM_ERROR, resolve_config
from tgexport.fetch.client import authenticate

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--session-path",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to write/read the session file.",
)
@click.option(
    "--tdata",
    "tdata_dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=None,
    help="Import the session from a Telegram Desktop tdata directory "
    "(e.g. ~/.local/share/TelegramDesktop/tdata) instead of logging in interactively. "
    "CLOSE Telegram Desktop during the import. "
    "Requires the 'tdata' extra: pip install 'tgexport[tdata]'.",
)
@click.option(
    "--passcode",
    "ask_passcode",
    is_flag=True,
    help="Prompt for the Telegram Desktop local passcode (only with --tdata).",
)
@click.option(
    "--reuse-current-session",
    is_flag=True,
    help="With --tdata: copy the desktop auth key instead of QR-logging a new "
    "authorization. Works offline, but Telegram logs out BOTH clients "
    "(AUTH_KEY_DUPLICATED) if tgexport and Telegram Desktop run at the same time.",
)
@click.pass_context
def login(
    ctx: click.Context,
    session_path: Path | None,
    tdata_dir: Path | None,
    ask_passcode: bool,
    reuse_current_session: bool,
) -> None:
    """Authenticate with Telegram and save the session for future runs."""
    cfg = resolve_config(ctx, require_api=tdata_dir is None)
    if session_path is not None:
        cfg = dataclasses.replace(cfg, session_path=session_path)
    try:
        if tdata_dir is not None:
            from tgexport.fetch.tdata import import_tdata_session

            passcode = getpass.getpass("Telegram Desktop passcode: ") if ask_passcode else None
            password: str | None = None
            if not reuse_current_session:
                password = (
                    getpass.getpass("2FA (cloud) password, Enter if none: ").strip() or None
                )
            asyncio.run(
                import_tdata_session(
                    tdata_dir,
                    cfg.session_path,
                    passcode,
                    proxies=cfg.proxies,
                    reuse_current=reuse_current_session,
                    password=password,
                )
            )
        else:
            asyncio.run(authenticate(cfg))
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("Authentication failed: %s", exc)
        sys.exit(EXIT_TELEGRAM_ERROR)
    click.echo(f"Session saved to {cfg.session_path}")
    if tdata_dir is not None:
        click.echo(
            "The session uses Telegram Desktop's API credentials: set "
            "TG_API_CREDENTIALS=desktop in your .env (TG_API_ID/TG_API_HASH not needed)."
        )
        if reuse_current_session:
            click.echo(
                "WARNING: this session shares Telegram Desktop's auth key. Using both "
                "at the same time will log you out of BOTH (AUTH_KEY_DUPLICATED)."
            )
