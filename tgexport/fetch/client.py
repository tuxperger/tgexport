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
import weakref
from contextlib import suppress
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


# Attributes the patches below install on the main client.
_CDN_CLIENTS_ATTR = "_tgexport_cdn_clients"
_CONNECTIONS_ATTR = "_tgexport_connections"
_SHUTDOWN_ATTR = "_tgexport_shutting_down"

# How long TelegramSession.__aexit__ may spend closing everything down before
# it gives up and lets the process exit. Telethon caps each socket close at 10s
# of its own, so this only bounds the pathological case.
_SHUTDOWN_TIMEOUT_SECONDS = 20.0


def track_connections(client: TelegramClient) -> None:
    """Register every connection the client opens so orphans can be found.

    Telethon builds a Connection in four places (client.connect,
    _create_exported_sender, the reconnect branch of _borrow_exported_sender
    and _get_cdn_client), always through the ``client._connection`` class, and
    hands it to MTProtoSender.connect, which spawns the connection's send and
    receive task pair. Only whoever ends up owning that sender can stop those
    tasks again — and a sender can be lost while it is still being built:
    _create_exported_sender connects first and only then awaits two round
    trips (auth export, InvokeWithLayer), so a download cancelled in between
    (the stall watchdog in fetch.media cancels transfers) drops the sole
    reference to a live connection. Nothing disconnects it afterwards, and its
    two tasks survive until the event loop is torn down — which is when
    asyncio reports "Task was destroyed but it is pending!".

    Subclassing the connection rather than wrapping the factory in a function
    is deliberate: TelegramClient.set_proxy calls issubclass() on it.
    """
    connection_cls = client._connection
    connections: list[weakref.ref[Any]] = []
    setattr(client, _CONNECTIONS_ATTR, connections)
    setattr(client, _SHUTDOWN_ATTR, False)
    client_ref = weakref.ref(client)

    def shutting_down() -> bool:
        owner = client_ref()
        return owner is None or bool(getattr(owner, _SHUTDOWN_ATTR, False))

    class TrackedConnection(connection_cls):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            connections.append(weakref.ref(self))

        async def connect(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            # Nothing would ever close a connection opened after the session
            # started shutting down: close_orphaned_connections has already
            # swept. Senders still try — MTProtoSender._start_reconnect fires
            # a task nobody holds on to — and a socket that comes up while
            # asyncio tears the loop down leaves its task pair pending.
            if shutting_down():
                raise ConnectionError("the Telegram client is shutting down")
            result = await super().connect(*args, **kwargs)
            if shutting_down():
                # Shutdown began while this socket was coming up.
                await self.disconnect()
                raise ConnectionError("the Telegram client is shutting down")
            return result

    TrackedConnection.__name__ = connection_cls.__name__
    TrackedConnection.__qualname__ = connection_cls.__qualname__
    client._connection = TrackedConnection

    original_create_sender = client._create_exported_sender

    async def create_exported_sender(dc_id: int) -> Any:  # noqa: ANN401
        try:
            return await original_create_sender(dc_id)
        except BaseException:
            # The half-built sender is unreachable from here on: its
            # connection gets closed now or never. Sweeping is safe inside
            # this call because _borrow_exported_sender holds
            # _borrow_sender_lock, so no other sender is being built.
            await close_orphaned_connections(client)
            raise

    client._create_exported_sender = create_exported_sender


def _senders(client: TelegramClient) -> list[Any]:
    """Every MTProtoSender reachable from the client: main, exported, CDN."""
    senders = [client._sender]
    senders.extend(sender for _, sender in client._borrowed_senders.values())
    senders.extend(
        cdn_client._sender for cdn_client in getattr(client, _CDN_CLIENTS_ATTR, {}).values()
    )
    return [sender for sender in senders if sender is not None]


def _owned_connections(client: TelegramClient) -> set[int]:
    """ids of the connections some reachable sender can still disconnect."""
    return {
        id(sender._connection)
        for sender in _senders(client)
        if getattr(sender, "_connection", None) is not None
    }


def begin_shutdown(client: TelegramClient) -> None:
    """Stop the client from opening (or reopening) any connection.

    Senders reconnect behind our back. A connection error makes
    MTProtoSender._start_reconnect fire a task nobody keeps a handle on, and
    that task calls Connection.connect again — including when the error is the
    cancellation of the connection's own loops while asyncio tears the event
    loop down. The freshly opened socket's send/receive tasks are then created
    after asyncio.run collected the set of tasks it cancels, so they are still
    pending when the loop closes: "Task was destroyed but it is pending!".

    Turning auto-reconnect off makes such a task disconnect its sender instead
    of reopening the socket (mtprotosender._reconnect: ``retries =
    self._retries if self._auto_reconnect else 0`` and an empty retry range
    falls through to _disconnect). The flag stops whatever still slips
    through — a sender we cannot reach, such as one lost mid-build.
    """
    setattr(client, _SHUTDOWN_ATTR, True)
    for sender in _senders(client):
        sender._auto_reconnect = False


async def close_orphaned_connections(client: TelegramClient) -> int:
    """Disconnect tracked connections no sender owns; return how many.

    A sender claims its connection (MTProtoSender.connect assigns it) before
    the connection is opened, so a live connection that no reachable sender
    points at is genuinely abandoned — as long as no sender is halfway through
    being built, which both callers guarantee: the _create_exported_sender
    wrapper runs under Telethon's _borrow_sender_lock, and TelegramSession
    sweeps only after everything has been disconnected.
    """
    owned = _owned_connections(client)
    connections: list[weakref.ref[Any]] = getattr(client, _CONNECTIONS_ATTR, [])
    # Work off a snapshot: other tasks are still running during shutdown and
    # append to this list (TrackedConnection.__init__) while we await below.
    snapshot = list(connections)
    keep: list[weakref.ref[Any]] = []
    closed = 0
    for ref in snapshot:
        connection = ref()
        if connection is None:
            continue  # garbage collected, nothing left to close
        if id(connection) in owned:
            # Still in use, and MTProtoSender._reconnect reopens the very same
            # object, so keep watching it even while it is down.
            keep.append(ref)
            continue
        if not getattr(connection, "_connected", False):
            continue  # unreachable and already closed: stop tracking it
        closed += 1
        logger.debug("Closing an abandoned connection to %s", connection)
        with suppress(Exception):
            await connection.disconnect()
    connections[:] = keep + connections[len(snapshot) :]
    if closed:
        logger.warning(
            "Closed %d connection(s) abandoned by a cancelled or failed transfer", closed
        )
    return closed


def patch_cdn_downloads(client: TelegramClient) -> None:
    """Repair Telethon 1.44's CDN download path (still broken in the latest release).

    Large files in public channels are served by CDN data centres: the main DC
    answers upload.getFile with upload.fileCdnRedirect, and the actual bytes
    must be fetched from the CDN DC. Telethon gets three things wrong there:

    1. downloads.py:54-57 — for a CDN download the target dc_id equals the CDN
       client's own session DC, so `_exported` comes out False and the sender
       is set to ``self.client._sender``, the *main* DC connection. The CDN
       DC never sees the request; the main DC rejects upload.getCdnFile with
       METHOD_INVALID.
    2. telegrambaseclient.py:917 — ``session.auth_key = self._sender.auth_key``
       writes the main DC's auth key into the cached CDN session, so every
       later CDN client connects to the CDN DC with a key that DC has never
       seen (AuthKeyNotFound) and the transfer hangs until the stall watchdog
       in fetch.media fires.
    3. The CDN client it builds is never disconnected — its sender and
       connection leak four asyncio tasks per attempt ("Task was destroyed
       but it is pending!").

    The replacement keeps one connected client per CDN DC on the main client;
    close_cdn_clients disconnects them when the session ends. Creation is
    serialised: several downloads run in parallel (one per chat worker, capped
    by the rate limiter), and without the lock they all miss the cache at once
    and every client but the last is dropped still connected.
    """
    from telethon import utils as tl_utils
    from telethon.client import downloads as tl_downloads

    cdn_clients: dict[int, TelegramClient] = {}
    cdn_lock = asyncio.Lock()
    setattr(client, _CDN_CLIENTS_ATTR, cdn_clients)

    async def connect_cdn_client(dc_id: int) -> TelegramClient:
        session = client._exported_sessions.get(dc_id)
        if not session:
            dc = await client._get_dc(dc_id, cdn=True)
            session = await tl_utils.maybe_async(client.session.clone())
            await tl_utils.maybe_async(session.set_dc(dc.id, dc.ip_address, dc.port))
            client._exported_sessions[dc_id] = session
        logger.info(
            "Connecting a CDN client to DC %s at %s:%s",
            dc_id,
            session.server_address,
            session.port,
        )
        cdn_client = client.__class__(
            session,
            api_id=client.api_id,
            api_hash=client.api_hash,
            connection=client._connection,
            proxy=client._proxy,
            timeout=client._timeout,
        )
        # Published before it is connected so close_orphaned_connections can
        # see who owns the connection while it is still being opened.
        cdn_clients[dc_id] = cdn_client
        # No auth_key assignment here: the CDN session starts empty, so the
        # sender negotiates a key with the CDN DC itself and stores it back
        # through the client's auth_key_callback.
        try:
            await cdn_client._sender.connect(
                client._connection(
                    session.server_address,
                    session.port,
                    session.dc_id,
                    loggers=client._log,
                    proxy=client._proxy,
                    local_addr=client._local_addr,
                )
            )
        except BaseException:
            # Cancelled (the stall watchdog in fetch.media) or failed midway:
            # take the half-built client back out and close whatever of it is
            # already up, or its connection loops run on unreferenced.
            cdn_clients.pop(dc_id, None)
            with suppress(Exception):
                await cdn_client._sender.disconnect()
            raise
        return cdn_client

    async def get_cdn_client(cdn_redirect: Any) -> TelegramClient:  # noqa: ANN401
        dc_id = cdn_redirect.dc_id
        async with cdn_lock:
            cached = cdn_clients.get(dc_id)
            if cached is not None:
                if cached.is_connected():
                    return cached
                # Evicting a client that died: close it out rather than
                # dropping it, or its connection loops stay pending.
                logger.debug("Replacing the dead CDN client for DC %s", dc_id)
                del cdn_clients[dc_id]
                with suppress(Exception):
                    await cached._sender.disconnect()
            return await connect_cdn_client(dc_id)

    client._get_cdn_client = get_cdn_client

    iter_cls = tl_downloads._DirectDownloadIter
    if getattr(iter_cls, "_tgexport_cdn_patched", False):
        return
    original_init = iter_cls._init
    original_close = iter_cls.close

    async def patched_init(self: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        result = await original_init(self, **kwargs)
        if self._cdn_redirect is not None:
            # Route upload.getCdnFile through the CDN client, not the main DC.
            self._sender = self._client._sender
        return result

    async def patched_close(self: Any) -> None:  # noqa: ANN401
        if getattr(self, "_cdn_redirect", None) is not None:
            # The CDN client is pooled and outlives this download.
            self._sender = None
            return
        await original_close(self)

    iter_cls._init = patched_init
    iter_cls.close = patched_close
    iter_cls._tgexport_cdn_patched = True


async def close_cdn_clients(client: TelegramClient) -> None:
    """Disconnect the CDN clients pooled by patch_cdn_downloads."""
    cdn_clients: dict[int, TelegramClient] = getattr(client, _CDN_CLIENTS_ATTR, {})
    # Drained rather than iterated: a download that is still in flight can add
    # a client to the pool while we await a disconnect.
    while cdn_clients:
        dc_id, cdn_client = cdn_clients.popitem()
        logger.debug("Disconnecting CDN client for DC %s", dc_id)
        # One CDN DC refusing to shut down cleanly must not cost us the rest
        # of the pool; close_orphaned_connections picks up whatever is left.
        with suppress(Exception):
            await cdn_client._sender.disconnect()


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


async def shutdown_client(client: TelegramClient) -> None:
    """Close everything a session opened, in dependency order."""
    begin_shutdown(client)
    try:
        await close_cdn_clients(client)
    finally:
        try:
            await client.disconnect()
        finally:
            # Last resort. Everything above only reaches senders that are
            # still referenced; a connection whose sender was lost while it
            # was being built would otherwise keep its send/receive loops
            # alive until the event loop is torn down, which is what prints
            # "Task was destroyed but it is pending!".
            await close_orphaned_connections(client)


async def _close_uninterruptibly(client: TelegramClient) -> None:
    """Run shutdown_client to the end even while the caller is being cancelled.

    Ctrl-C does not simply stop the program: asyncio.run cancels the task that
    runs the export, and in a task that is already cancelled every ``await``
    in the cleanup raises CancelledError at once — so the plain
    ``await client.disconnect()`` never got to run a single one of its steps.
    Every socket stayed open, and their send/receive task pairs were still
    pending when the loop closed. Giving the cleanup a task of its own and
    shielding it lets it finish; the cancellation is re-raised afterwards, so
    the CLI still sees the interrupt it is waiting for.
    """
    task = asyncio.ensure_future(
        asyncio.wait_for(shutdown_client(client), _SHUTDOWN_TIMEOUT_SECONDS)
    )
    interrupted: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as cancellation:
            # Only the caller can be cancelled here, nobody else holds `task`.
            # A second Ctrl-C does not cancel again either — it raises
            # KeyboardInterrupt out of the event loop — so this cannot spin.
            if interrupted is None:
                interrupted = cancellation
        except TimeoutError:
            logger.warning(
                "Gave up closing the Telegram connections after %.0fs",
                _SHUTDOWN_TIMEOUT_SECONDS,
            )
        except Exception:
            # Never raised onwards: __aexit__ would replace whatever the
            # caller was already failing with.
            logger.exception("Closing the Telegram connections failed")
    if interrupted is not None:
        # Collect the result so a failure that raced the cancellation cannot
        # resurface as an unretrieved task exception.
        with suppress(BaseException):
            task.result()
        raise interrupted


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
        track_connections(client)
        patch_cdn_downloads(client)
        await check_mtproto_reachable(client, proxy)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                await _interactive_login(client)
            _restrict_session_permissions(cfg.session_path)
        except BaseException:
            # __aexit__ never runs for a context manager that failed to enter,
            # so a login that raises (Ctrl-C at the code prompt, a rejected
            # password) would otherwise leave this client connected.
            await _close_uninterruptibly(client)
            raise
        self._client = client
        return client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        client = self._client
        if client is None:
            return
        self._client = None
        await _close_uninterruptibly(client)


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
