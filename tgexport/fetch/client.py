"""Telethon client wrapper: session management and interactive authentication.

This layer is strictly read-only towards Telegram (constitution §I): the client
is used exclusively for iter_dialogs / iter_messages / download_media.
"""

from __future__ import annotations

import asyncio
import getpass
import logging
import os
import stat
from pathlib import Path
from collections.abc import Sequence
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

from telethon import TelegramClient

from tgexport.core.config import Config

logger = logging.getLogger(__name__)


# Telethon's defaults for a fresh session that has no DC recorded yet.
_DEFAULT_DC = (2, "149.154.167.51", 443)


def _probe_target(
    dc_id: int, server_address: str | None, port: int | None, proxy_url: str | None
) -> tuple[str, int, str]:
    """Return (host, port, human description) of the first hop we must reach.

    With a proxy that is the proxy itself; otherwise the Telegram DC stored in
    the session (or Telethon's default DC for a fresh session).
    """
    if not server_address or not port:
        dc_id, server_address, port = _DEFAULT_DC
    if proxy_url:
        parsed = urlsplit(proxy_url)
        assert parsed.hostname and parsed.port  # validated by proxy_client_kwargs
        description = (
            f"Telegram DC {dc_id} ({server_address}:{port}) "
            f"via {parsed.scheme} proxy {parsed.hostname}:{parsed.port}"
        )
        return parsed.hostname, parsed.port, description
    return server_address, port, f"Telegram DC {dc_id} ({server_address}:{port}) directly"


async def check_mtproto_reachable(client: TelegramClient, proxy_url: str | None) -> None:
    """Log the connection target and fail fast if it is not reachable over TCP.

    Raises ConnectionError with an actionable message instead of letting
    Telethon spend a minute on connect retries.
    """
    session = client.session
    host, port, description = _probe_target(
        session.dc_id, session.server_address, session.port, proxy_url
    )
    logger.info("Connecting to %s", description)
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
    except (OSError, TimeoutError) as exc:
        hint = (
            "check the TG_PROXY setting in .env"
            if proxy_url
            else "if Telegram is blocked on your network, set TG_PROXY in .env"
        )
        raise ConnectionError(
            f"MTProto endpoint {host}:{port} is unreachable over TCP ({exc!r}); {hint}."
        ) from exc
    writer.close()
    await writer.wait_closed()


def instrument_dc_logging(client: TelegramClient) -> None:
    """Log every data-center lookup with its address, including CDN ones.

    Telethon resolves both migrated-DC and CDN download targets through
    client._get_dc; wrapping it is the only place that sees the actual
    server address a file is fetched from.
    """
    original = client._get_dc

    async def logged_get_dc(dc_id: int, cdn: bool = False) -> Any:  # noqa: ANN401
        dc = await original(dc_id, cdn)
        logger.info(
            "Using %sDC %s at %s:%s", "CDN " if cdn else "", dc_id, dc.ip_address, dc.port
        )
        return dc

    client._get_dc = logged_get_dc


def proxy_client_kwargs(proxy_url: str | None) -> dict[str, Any]:
    """Translate a TG_PROXY URL into TelegramClient constructor kwargs.

    Supports socks5/socks4/http (needs python-socks) and mtproxy://SECRET@host:port.
    """
    if not proxy_url:
        return {}
    parsed = urlsplit(proxy_url)
    scheme = parsed.scheme.lower()
    if not parsed.hostname or not parsed.port:
        raise ValueError(f"TG_PROXY must include host and port, got {proxy_url!r}")
    if scheme in ("mtproxy", "mtproto"):
        if not parsed.username:
            raise ValueError("mtproxy:// URL must look like mtproxy://SECRET@host:port")
        from telethon.network import connection as tl_connection

        return {
            "connection": tl_connection.ConnectionTcpMTProxyRandomizedIntermediate,
            "proxy": (parsed.hostname, parsed.port, parsed.username),
        }
    if scheme in ("socks5", "socks4", "http"):
        proxy: dict[str, Any] = {
            "proxy_type": scheme,
            "addr": parsed.hostname,
            "port": parsed.port,
            "rdns": True,
        }
        if parsed.username:
            proxy["username"] = parsed.username
        if parsed.password:
            proxy["password"] = parsed.password
        return {"proxy": proxy}
    raise ValueError(
        f"Unsupported TG_PROXY scheme {scheme!r}; use socks5://, socks4://, http:// or mtproxy://"
    )


async def pick_working_proxy(proxies: Sequence[str]) -> str | None:
    """Return the first proxy from TG_PROXY that accepts TCP connections.

    Returns None when no proxies are configured (direct connection). All URLs
    are validated up front so a typo in a fallback proxy surfaces immediately,
    not only when the proxies before it go down. Raises ConnectionError when
    every configured proxy is unreachable.
    """
    for url in proxies:
        proxy_client_kwargs(url)
    failures = []
    for url in proxies:
        parsed = urlsplit(url)
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(parsed.hostname, parsed.port), timeout=5
            )
        except (OSError, TimeoutError) as exc:
            logger.warning(
                "Proxy %s:%s is unreachable (%r), trying the next one",
                parsed.hostname,
                parsed.port,
                exc,
            )
            failures.append(f"{parsed.hostname}:{parsed.port} ({exc!r})")
            continue
        writer.close()
        await writer.wait_closed()
        return url
    if not proxies:
        return None
    raise ConnectionError(
        "None of the proxies in TG_PROXY are reachable over TCP: " + "; ".join(failures)
    )


def _restrict_session_permissions(session_path: Path) -> None:
    if session_path.suffix != ".session":
        session_file = session_path.with_suffix(".session")
    else:
        session_file = session_path
    if session_file.exists():
        os.chmod(session_file, stat.S_IRUSR | stat.S_IWUSR)


class TelegramSession:
    """Async context manager yielding a connected, authorised TelegramClient."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._client: TelegramClient | None = None

    async def __aenter__(self) -> TelegramClient:
        cfg = self._cfg
        cfg.session_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(cfg.session_path.parent, stat.S_IRWXU)
        proxy = await pick_working_proxy(cfg.proxies)
        # api_id/api_hash must be keyword arguments: importing opentele (done by
        # load_config for TG_API_CREDENTIALS=desktop) monkeypatches
        # TelegramClient.__init__ with an extra positional `api` parameter, which
        # silently misassigns positional credentials (api_hash ends up an int).
        client = TelegramClient(
            str(cfg.session_path),
            api_id=cfg.api_id,
            api_hash=cfg.api_hash,
            **proxy_client_kwargs(proxy),
        )
        instrument_dc_logging(client)
        await check_mtproto_reachable(client, proxy)
        await client.connect()
        if not await client.is_user_authorized():
            await _interactive_login(client)
        _restrict_session_permissions(cfg.session_path)
        self._client = client
        return client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


async def _interactive_login(client: TelegramClient) -> None:
    """Phone → confirmation code → optional 2FA password."""
    phone = input("Phone number (international format, e.g. +79991234567): ").strip()
    await client.send_code_request(phone)
    code = input("Confirmation code from Telegram: ").strip()
    try:
        await client.sign_in(phone=phone, code=code)
    except Exception as exc:  # SessionPasswordNeededError is raised for 2FA accounts
        if type(exc).__name__ != "SessionPasswordNeededError":
            raise
        password = getpass.getpass("2FA password: ")
        await client.sign_in(password=password)
    logger.info("Authentication successful")


async def authenticate(cfg: Config) -> None:
    """Run the interactive login flow and persist the session file (mode 600)."""
    async with TelegramSession(cfg):
        pass
