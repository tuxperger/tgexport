"""Unit tests for session shutdown: no connection may outlive the session."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest
from telethon.network.mtprotosender import MTProtoSender

from tgexport.core.config import Config
from tgexport.fetch.client import (
    TelegramSession,
    begin_shutdown,
    close_orphaned_connections,
    track_connections,
)


class _FakeConnection:
    """Stands in for telethon's Connection, whose connect() spawns two tasks."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._connected = False
        self.connects = 0
        self.disconnects = 0
        # Set to make connect() block, standing in for a slow socket.
        self.gate: asyncio.Event | None = None

    async def connect(self, timeout: float | None = None, ssl: Any = None) -> None:
        if self.gate is not None:
            await self.gate.wait()
        self.connects += 1
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        self.disconnects += 1


class _FakeSender:
    def __init__(self) -> None:
        self._connection: _FakeConnection | None = None
        self._auto_reconnect = True

    async def connect(self, connection: _FakeConnection) -> None:
        self._connection = connection
        await connection.connect()

    async def disconnect(self) -> None:
        if self._connection is not None:
            await self._connection.disconnect()
            self._connection = None


class _FakeClient:
    """Exposes only what the shutdown path touches on a TelegramClient."""

    def __init__(self) -> None:
        self._connection: Any = _FakeConnection
        self._sender = _FakeSender()
        self._borrowed_senders: dict[int, tuple[object, _FakeSender]] = {}
        self.disconnected = False

    async def _create_exported_sender(self, dc_id: int) -> _FakeSender:
        sender = _FakeSender()
        await sender.connect(self._connection())
        self._borrowed_senders[dc_id] = (object(), sender)
        return sender

    def disconnect(self) -> asyncio.Future[None]:
        # Telethon returns an awaitable (a shielded task), not a coroutine.
        async def _run() -> None:
            self.disconnected = True
            await self._sender.disconnect()
            for _, sender in self._borrowed_senders.values():
                await sender.disconnect()
            self._borrowed_senders.clear()

        return asyncio.ensure_future(_run())


def _tracked_client() -> _FakeClient:
    client = _FakeClient()
    track_connections(client)  # type: ignore[arg-type]
    return client


async def _connected(client: _FakeClient) -> _FakeConnection:
    connection: _FakeConnection = client._connection()
    await connection.connect()
    return connection


def _config(tmp_path: Path) -> Config:
    return Config(
        api_id=1,
        api_hash="hash",
        session_path=tmp_path / "session",
        db_path=tmp_path / "archive.db",
        media_dir=tmp_path / "media",
        output_dir=tmp_path / "output",
    )


async def test_connect_is_refused_once_shutdown_started() -> None:
    client = _tracked_client()
    begin_shutdown(client)  # type: ignore[arg-type]
    connection = client._connection()

    with pytest.raises(ConnectionError):
        await connection.connect()

    assert not connection._connected


async def test_connection_coming_up_during_shutdown_closes_itself() -> None:
    """A sender reconnecting behind our back must not leave a live socket."""
    client = _tracked_client()
    connection: _FakeConnection = client._connection()
    connection.gate = asyncio.Event()
    connecting = asyncio.ensure_future(connection.connect())
    await asyncio.sleep(0)  # let it reach the blocking part of connect()

    begin_shutdown(client)  # type: ignore[arg-type]
    connection.gate.set()

    with pytest.raises(ConnectionError):
        await connecting
    assert not connection._connected
    assert connection.disconnects == 1


async def test_begin_shutdown_stops_every_sender_from_reconnecting() -> None:
    client = _tracked_client()
    exported = await client._create_exported_sender(4)
    cdn_sender = _FakeSender()
    setattr(client, "_tgexport_cdn_clients", {203: type("C", (), {"_sender": cdn_sender})()})

    begin_shutdown(client)  # type: ignore[arg-type]

    assert not client._sender._auto_reconnect
    assert not exported._auto_reconnect
    assert not cdn_sender._auto_reconnect


class _Loggers(dict[str, logging.Logger]):
    def __missing__(self, key: str) -> logging.Logger:
        logger = logging.getLogger(f"tests.silenced.{key}")
        # One half of the test below makes a real reconnect fail on purpose.
        logger.disabled = True
        self[key] = logger
        return logger


async def test_auto_reconnect_is_what_reopens_a_closed_socket() -> None:
    """Pins the telethon behaviour begin_shutdown relies on.

    MTProtoSender._reconnect runs in a task nobody holds on to, so it can fire
    at any moment — including while the event loop is being torn down. With
    auto-reconnect left on it calls Connection.connect again, and that is how
    a socket (and its pending send/receive task pair) outlives the session.
    """
    reopened = {}
    for auto_reconnect in (True, False):
        sender = MTProtoSender(
            None, loggers=_Loggers(), auto_reconnect=auto_reconnect, retries=1, delay=0
        )
        connection = _FakeConnection()
        sender._connection = connection
        sender._user_connected = True

        await sender._reconnect(ConnectionError("the server dropped us"))

        reopened[auto_reconnect] = connection.connects
    assert reopened == {True: 1, False: 0}


async def test_sweep_closes_only_the_connections_no_sender_owns() -> None:
    client = _tracked_client()
    owned = await _connected(client)
    client._sender._connection = owned
    orphan = await _connected(client)

    closed = await close_orphaned_connections(client)  # type: ignore[arg-type]

    assert closed == 1
    assert orphan.disconnects == 1
    assert owned.disconnects == 0


async def test_cleanup_completes_even_when_the_caller_is_cancelled(tmp_path: Path) -> None:
    """Ctrl-C cancels the export task; its cleanup must still run to the end.

    In a cancelled task every await raises CancelledError at once, so an
    unshielded ``await client.disconnect()`` closes nothing and the sockets'
    task pairs are still pending when the loop is torn down.
    """
    client = _tracked_client()
    orphan = await _connected(client)
    session = TelegramSession(_config(tmp_path))
    session._client = client  # type: ignore[assignment]

    async def use_session() -> None:
        try:
            await asyncio.sleep(3600)
        finally:
            await session.__aexit__(None, None, None)

    task = asyncio.ensure_future(use_session())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.disconnected
    assert orphan.disconnects == 1
    assert getattr(client, "_tgexport_shutting_down") is True
