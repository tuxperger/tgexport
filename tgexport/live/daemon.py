"""Live-mode daemon skeleton. NOT implemented in this release.

The signature below is a frozen contract (see
specs/001-telegram-profile-export/contracts/layer-interfaces.md): implementing
live mode later only requires filling in the body — cli/cmd_watch.py and the
layer boundaries stay unchanged.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from tgexport.core.config import Config


async def run_daemon(
    cfg: Config,
    conn: aiosqlite.Connection,
    output: Path,
) -> None:
    """Keep the session open and mirror real-time events into the archive.

    Planned implementation (TODO(live)):
      1. Register Telethon handlers for NewMessage, MessageEdited, MessageDeleted.
      2. On each event, map to RawMessage and call storage.repos.messages.upsert_message
         (deletions: mark is_deleted=1; edits: append to message_edits).
      3. Call render.renderer.render_chat for the affected chat only.
      4. Run until cancelled.
    """
    raise NotImplementedError("Live mode is not implemented in this release.")
