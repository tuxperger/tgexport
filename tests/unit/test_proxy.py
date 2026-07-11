"""Unit tests for TG_PROXY URL parsing, connectivity probe targets and failover."""

from __future__ import annotations

import asyncio

import pytest

from tgexport.fetch.client import _probe_target, pick_working_proxy, proxy_client_kwargs


def test_no_proxy() -> None:
    assert proxy_client_kwargs(None) == {}
    assert proxy_client_kwargs("") == {}


def test_socks5_with_auth() -> None:
    kwargs = proxy_client_kwargs("socks5://user:secret@127.0.0.1:1080")
    assert kwargs == {
        "proxy": {
            "proxy_type": "socks5",
            "addr": "127.0.0.1",
            "port": 1080,
            "rdns": True,
            "username": "user",
            "password": "secret",
        }
    }


def test_http_without_auth() -> None:
    kwargs = proxy_client_kwargs("http://proxy.local:3128")
    assert kwargs["proxy"]["proxy_type"] == "http"
    assert "username" not in kwargs["proxy"]


def test_mtproxy() -> None:
    kwargs = proxy_client_kwargs("mtproxy://ee1234abcd@1.2.3.4:443")
    assert kwargs["proxy"] == ("1.2.3.4", 443, "ee1234abcd")
    assert "connection" in kwargs


def test_mtproxy_without_secret() -> None:
    with pytest.raises(ValueError, match="SECRET"):
        proxy_client_kwargs("mtproxy://1.2.3.4:443")


def test_missing_port() -> None:
    with pytest.raises(ValueError, match="host and port"):
        proxy_client_kwargs("socks5://127.0.0.1")


def test_unsupported_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        proxy_client_kwargs("ftp://127.0.0.1:21")


def test_probe_target_direct() -> None:
    host, port, description = _probe_target(2, "149.154.167.51", 443, None)
    assert (host, port) == ("149.154.167.51", 443)
    assert "DC 2" in description and "directly" in description


def test_probe_target_fresh_session_uses_default_dc() -> None:
    host, port, description = _probe_target(0, None, None, None)
    assert (host, port) == ("149.154.167.51", 443)
    assert "DC 2" in description


async def _local_listener() -> tuple[asyncio.Server, int]:
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _free_port() -> int:
    server, port = await _local_listener()
    server.close()
    await server.wait_closed()
    return port


async def test_pick_working_proxy_empty_means_direct() -> None:
    assert await pick_working_proxy(()) is None


async def test_pick_working_proxy_skips_unreachable() -> None:
    dead_port = await _free_port()
    server, live_port = await _local_listener()
    try:
        picked = await pick_working_proxy(
            (f"socks5://127.0.0.1:{dead_port}", f"socks5://127.0.0.1:{live_port}")
        )
    finally:
        server.close()
        await server.wait_closed()
    assert picked == f"socks5://127.0.0.1:{live_port}"


async def test_pick_working_proxy_all_unreachable() -> None:
    port = await _free_port()
    with pytest.raises(ConnectionError, match="None of the proxies"):
        await pick_working_proxy((f"socks5://127.0.0.1:{port}",))


async def test_pick_working_proxy_validates_all_urls_first() -> None:
    server, live_port = await _local_listener()
    try:
        # The bad fallback URL must fail even though the first proxy is reachable.
        with pytest.raises(ValueError, match="scheme"):
            await pick_working_proxy(
                (f"socks5://127.0.0.1:{live_port}", "ftp://127.0.0.1:21")
            )
    finally:
        server.close()
        await server.wait_closed()


def test_probe_target_via_proxy_hides_credentials() -> None:
    host, port, description = _probe_target(
        4, "149.154.167.91", 443, "socks5://user:secret@10.0.0.1:1080"
    )
    assert (host, port) == ("10.0.0.1", 1080)
    assert "DC 4" in description and "socks5 proxy 10.0.0.1:1080" in description
    assert "secret" not in description
