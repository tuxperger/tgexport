"""Attachment download. No storage access here (constitution §IV):
the caller (CLI layer) owns deduplication decisions and DB writes.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from telethon import TelegramClient
from telethon import utils as tl_utils

from tgexport.fetch.rate_limiter import RateLimiter, call_with_retry

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

# A transfer that produced no bytes for this long is considered stalled and is
# cancelled so call_with_retry can restart it (Telethon's cross-DC download
# connections can die silently, leaving download_media awaiting forever).
STALL_TIMEOUT_SECONDS = 60.0


class MediaDownloadError(Exception):
    """Raised when an attachment cannot be downloaded after retries."""


def _sanitise(name: str) -> str:
    return _SAFE_NAME.sub("_", name)[:80]


def sharded_media_path(media_dir: Path, chat_id: int, sha256: str, filename: str | None) -> Path:
    """media/<chat_id>/<sha256[:2]>/<sha256[2:]>_<name> — two-level prefix sharding."""
    suffix = _sanitise(filename) if filename else "file"
    return media_dir / str(chat_id) / sha256[:2] / f"{sha256[2:]}_{suffix}"


def _human_size(size: int | None) -> str:
    if size is None:
        return "unknown size"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


async def download_attachment(
    client: TelegramClient,
    rate_limiter: RateLimiter,
    chat_id: int,
    message_id: int,
    tmp_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> tuple[Path, str] | None:
    """Download the media of one message to a temp file.

    Returns (tmp_path, sha256_hex), or None if the message has no
    downloadable media. The caller decides whether to keep the file
    (new hash) or discard it (duplicate) and where to move it.

    progress_callback receives (bytes_received, bytes_total) during the
    transfer (Telethon's download_media contract). status_callback receives
    a short phase description before the transfer starts, so the caller can
    show where a slow download is actually spending its time.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)

    def _status(phase: str) -> None:
        if status_callback is not None:
            status_callback(phase)

    async def _fetch() -> Path | None:
        _status("resolving message")
        message = await client.get_messages(chat_id, ids=message_id)
        if message is None or message.media is None:
            logger.warning("chat=%d msg=%d: no downloadable media (deleted?)", chat_id, message_id)
            return None
        try:
            media_dc = tl_utils.get_input_location(message.media)[0]
        except TypeError:
            media_dc = None
        file = message.file
        logger.info(
            "Downloading %s (%s, %s) chat=%d msg=%d from DC %s",
            (file.name if file else None) or "media",
            (file.mime_type if file else None) or "unknown type",
            _human_size(file.size if file else None),
            chat_id,
            message_id,
            media_dc if media_dc is not None else "of current session",
        )
        _status("waiting for download slot")
        async with rate_limiter.acquire_download():
            _status("starting transfer")
            fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, prefix="dl_")
            os.close(fd)

            last_activity = time.monotonic()

            def _tracked_progress(current: int, total: int) -> None:
                nonlocal last_activity
                last_activity = time.monotonic()
                if progress_callback is not None:
                    progress_callback(current, total)

            transfer = asyncio.ensure_future(
                client.download_media(message, file=tmp_name, progress_callback=_tracked_progress)
            )
            try:
                while True:
                    try:
                        result = await asyncio.wait_for(
                            asyncio.shield(transfer), timeout=STALL_TIMEOUT_SECONDS
                        )
                        break
                    except TimeoutError:
                        idle = time.monotonic() - last_activity
                        if idle < STALL_TIMEOUT_SECONDS:
                            continue  # bytes are still flowing, just a long transfer
                        raise TimeoutError(
                            f"transfer stalled: no data for {idle:.0f}s "
                            f"(chat={chat_id} msg={message_id})"
                        ) from None
            except BaseException:
                transfer.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await transfer
                Path(tmp_name).unlink(missing_ok=True)
                raise
            if result is None:
                Path(tmp_name).unlink(missing_ok=True)
                return None
            return Path(result)

    def _describe() -> str:
        # Deliberately no DC here: session.dc_id is the main DC, which is not
        # where a migrated or CDN-hosted file actually comes from. The real
        # address is logged by instrument_dc_logging.
        return f"download chat={chat_id} msg={message_id}"

    tmp_path = await call_with_retry(_fetch, description=_describe)
    if tmp_path is None:
        return None

    _status("hashing")
    digest = hashlib.sha256()
    with open(tmp_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
    return tmp_path, digest.hexdigest()
