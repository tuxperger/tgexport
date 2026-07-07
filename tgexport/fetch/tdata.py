"""Import an existing Telegram Desktop session (tdata folder) via opentele.

The resulting Telethon session keeps Telegram Desktop's API credentials, so
subsequent runs must use TG_API_CREDENTIALS=desktop instead of TG_API_ID /
TG_API_HASH (see core.config._desktop_api_credentials).

Two modes:
- new session (default): QR-login a separate authorization through the desktop
  account. Independent auth key, safe to use while Telegram Desktop runs.
- reuse current: copy the desktop's auth key. Telegram revokes a key it sees
  from two connections at once (AUTH_KEY_DUPLICATED), logging out BOTH tgexport
  and Telegram Desktop — only use this if the desktop client stays closed.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

from tgexport.fetch.client import (
    _restrict_session_permissions,
    check_mtproto_reachable,
    proxy_client_kwargs,
)

logger = logging.getLogger(__name__)


class TdataImportError(RuntimeError):
    """Raised when a Telegram Desktop session cannot be imported."""


def _patch_opentele_map_read() -> None:
    """Make opentele tolerate map entries added by newer Telegram Desktop.

    opentele 1.15.1 predates entry types >= 0x17 (custom emoji keys, webview
    tokens, ...) and raises TDataReadMapDataFailed on the first one it meets,
    which surfaces as "No account has been loaded". Session conversion never
    uses map contents (auth keys come from the separate mtp data file), so an
    unknown entry is treated as end-of-map instead of a failure.
    """
    from opentele.exception import TDataReadMapDataFailed
    from opentele.td.account import MapData

    original = MapData.read
    if getattr(original, "_tgexport_patched", False):
        return

    def read(self: Any, localKey: Any, legacyPasscode: Any) -> Any:  # noqa: ANN401
        try:
            return original(self, localKey, legacyPasscode)
        except TDataReadMapDataFailed as exc:
            if "Unknown key type" not in str(exc):
                raise
            logger.debug("Ignoring unsupported tdata map entries: %s", exc)
            # What MapData.read would have set after its parse loop.
            self._MapData__localKey = localKey
            if not hasattr(self, "_oldMapVersion"):
                self._oldMapVersion = 0

    read._tgexport_patched = True  # type: ignore[attr-defined]
    MapData.read = read


def _load_tdesktop(tdata_dir: Path, passcode: str | None) -> Any:  # noqa: ANN401
    from opentele.td import TDesktop

    tdesk = TDesktop(str(tdata_dir), passcode=passcode)
    if not tdesk.isLoaded():
        raise TdataImportError(
            f"Could not load accounts from {tdata_dir}. If Telegram Desktop has a "
            "local passcode, re-run with --passcode."
        )
    return tdesk


async def import_tdata_session(
    tdata_dir: Path,
    session_path: Path,
    passcode: str | None = None,
    proxy: str | None = None,
    *,
    reuse_current: bool = False,
    password: str | None = None,
) -> None:
    """Create a Telethon session file at session_path from a tdata directory.

    Default: QR-login a NEW independent authorization (requires connectivity;
    2FA accounts need `password`). With reuse_current=True the desktop auth key
    is copied instead — works offline, but must not be used while Telegram
    Desktop is running (AUTH_KEY_DUPLICATED logs out both).
    """
    try:
        from opentele.api import CreateNewSession, UseCurrentSession
        from opentele.exception import OpenTeleException
    except ImportError as exc:
        raise TdataImportError(
            "opentele is not installed. Install it with: pip install 'tgexport[tdata]'"
        ) from exc

    _patch_opentele_map_read()

    logger.warning(
        "Close Telegram Desktop while importing: the desktop auth key is used "
        "during the import, and Telegram revokes a key used from two "
        "connections at once (AUTH_KEY_DUPLICATED), logging the desktop out."
    )

    if not tdata_dir.is_dir():
        raise TdataImportError(f"tdata directory not found: {tdata_dir}")
    session_path.parent.mkdir(parents=True, exist_ok=True)
    proxy_kwargs = proxy_client_kwargs(proxy)

    try:
        tdesk = _load_tdesktop(tdata_dir, passcode)
        authorized: bool | None
        if reuse_current:
            client = await tdesk.ToTelethon(
                session=str(session_path), flag=UseCurrentSession, **proxy_kwargs
            )
            # ToTelethon fills the session but telethon only commits it on a
            # successful connect; save explicitly so the file survives offline runs.
            client.session.save()
            try:
                await check_mtproto_reachable(client, proxy)
                await client.connect()
            except (ConnectionError, TimeoutError) as exc:
                logger.warning(
                    "Session converted, but Telegram is unreachable to verify it (%s). "
                    "If Telegram is blocked on your network, set TG_PROXY in .env.",
                    exc,
                )
                authorized = None
            else:
                try:
                    authorized = await client.is_user_authorized()
                finally:
                    await client.disconnect()
        else:
            # A previous (possibly revoked) session file would be picked up by
            # the QR login flow instead of creating a fresh authorization.
            if session_path.exists():
                logger.info("Replacing existing session file %s", session_path)
                session_path.unlink()
            with warnings.catch_warnings():
                # opentele calls telethon's (now async) _on_login without await;
                # harmless for us, but noisy.
                warnings.filterwarnings(
                    "ignore",
                    message="coroutine 'AuthMethods._on_login' was never awaited",
                    category=RuntimeWarning,
                )
                client = await tdesk.ToTelethon(
                    session=str(session_path),
                    flag=CreateNewSession,
                    password=password,
                    **proxy_kwargs,
                )
                # Returned client is connected under the new, independent auth key.
                client.session.save()
                try:
                    # The skipped _on_login also leaves the client's cached
                    # authorization flag stale, so verify with a real request.
                    authorized = (await client.get_me()) is not None
                finally:
                    await client.disconnect()
    except OpenTeleException as exc:
        raise TdataImportError(f"Failed to import tdata session: {exc}") from exc

    if authorized is False:
        raise TdataImportError(
            "The imported session is not authorized. Make sure Telegram Desktop is "
            "logged in, then try again."
        )
    _restrict_session_permissions(session_path)
    logger.info(
        "Imported Telegram Desktop session to %s (%s)",
        session_path,
        "shared auth key — do not run tgexport and Telegram Desktop at the same time"
        if reuse_current
        else "independent authorization, safe to use alongside Telegram Desktop",
    )
