"""Unit tests for TG_PROXY URL parsing and connectivity probe targets."""

from __future__ import annotations

import pytest

from tgexport.fetch.client import _probe_target, proxy_client_kwargs


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


def test_probe_target_via_proxy_hides_credentials() -> None:
    host, port, description = _probe_target(
        4, "149.154.167.91", 443, "socks5://user:secret@10.0.0.1:1080"
    )
    assert (host, port) == ("10.0.0.1", 1080)
    assert "DC 4" in description and "socks5 proxy 10.0.0.1:1080" in description
    assert "secret" not in description
