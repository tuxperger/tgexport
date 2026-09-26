"""Unit tests for the CDN client pool installed by patch_cdn_downloads."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from telethon.client import downloads as tl_downloads

from tgexport.fetch.client import close_cdn_clients, patch_cdn_downloads


class _FakeSession:
    def __init__(self) -> None:
        self.dc_id = 0
        self.server_address = ""
        self.port = 0

    def clone(self) -> _FakeSession:
        return _FakeSession()

    def set_dc(self, dc_id: int, server_address: str, port: int) -> None:
        self.dc_id, self.server_address, self.port = dc_id, server_address, port


class _FakeSender:
    def __init__(self) -> None:
        self.connected = False
        self.disconnects = 0

    async def connect(self, connection: object) -> None:
        await asyncio.sleep(0)  # yield, so a missing lock lets callers interleave
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnects += 1
        self.connected = False


class _FakeClient:
    """Stands in for TelegramClient, exposing only what the pool touches."""

    created: list[_FakeClient] = []

    def __init__(
        self,
        session: _FakeSession | None = None,
        *,
        api_id: int = 1,
        api_hash: str = "hash",
        connection: Any = None,
        proxy: object = None,
        timeout: int = 10,
    ) -> None:
        self.session = session if session is not None else _FakeSession()
        if connection is not None:
            # The pool hands the CDN client the main client's connection class.
            self._connection = connection  # type: ignore[method-assign]
        self.api_id = api_id
        self.api_hash = api_hash
        self._proxy = proxy
        self._timeout = timeout
        self._local_addr = None
        self._log: dict[str, Any] = {}
        self._exported_sessions: dict[int, _FakeSession] = {}
        self._sender = _FakeSender()
        _FakeClient.created.append(self)

    def _connection(self, *args: Any, **kwargs: Any) -> object:
        return object()

    async def _get_dc(self, dc_id: int, cdn: bool = False) -> SimpleNamespace:
        await asyncio.sleep(0)  # the real lookup awaits the network
        return SimpleNamespace(id=dc_id, ip_address="91.105.192.100", port=443)

    def is_connected(self) -> bool:
        return self._sender.connected


def _fresh_client() -> _FakeClient:
    _FakeClient.created.clear()
    client = _FakeClient()
    patch_cdn_downloads(client)  # type: ignore[arg-type]
    _FakeClient.created.clear()  # only count the CDN clients built from here on
    return client


async def test_concurrent_redirects_share_one_cdn_client() -> None:
    """Parallel downloads hitting the same CDN DC must not each build a client.

    Without serialisation every caller misses the cache, and all but the last
    client is dropped while still connected — leaking its connection loops.
    """
    client = _fresh_client()
    redirect = SimpleNamespace(dc_id=203)

    clients = await asyncio.gather(*(client._get_cdn_client(redirect) for _ in range(5)))

    assert len({id(c) for c in clients}) == 1
    assert len(_FakeClient.created) == 1


async def test_separate_cdn_dcs_get_separate_clients() -> None:
    client = _fresh_client()

    first = await client._get_cdn_client(SimpleNamespace(dc_id=203))
    second = await client._get_cdn_client(SimpleNamespace(dc_id=204))

    assert first is not second
    assert len(_FakeClient.created) == 2


async def test_dead_cdn_client_is_closed_before_replacement() -> None:
    client = _fresh_client()
    redirect = SimpleNamespace(dc_id=203)

    first = await client._get_cdn_client(redirect)
    first._sender.connected = False  # the CDN DC dropped us
    second = await client._get_cdn_client(redirect)

    assert second is not first
    assert first._sender.disconnects == 1


async def test_close_cdn_clients_empties_the_pool() -> None:
    client = _fresh_client()

    first = await client._get_cdn_client(SimpleNamespace(dc_id=203))
    second = await client._get_cdn_client(SimpleNamespace(dc_id=204))
    await close_cdn_clients(client)  # type: ignore[arg-type]

    assert first._sender.disconnects == 1
    assert second._sender.disconnects == 1
    assert client._tgexport_cdn_clients == {}


async def test_close_leaves_the_pooled_cdn_sender_connected() -> None:
    """A finished CDN download must not disconnect the shared client."""
    _fresh_client()
    download_iter = SimpleNamespace(_cdn_redirect=SimpleNamespace(dc_id=203), _sender="cdn")

    await tl_downloads._DirectDownloadIter.close(download_iter)

    assert download_iter._sender is None
