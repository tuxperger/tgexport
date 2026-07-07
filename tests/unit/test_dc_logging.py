"""Unit tests for data-center lookup logging (instrument_dc_logging)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tgexport.fetch.client import instrument_dc_logging


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[int, bool]] = []

    async def _get_dc(self, dc_id: int, cdn: bool = False) -> SimpleNamespace:
        self.calls.append((dc_id, cdn))
        return SimpleNamespace(id=dc_id, ip_address="91.108.56.1", port=443)


async def test_logs_regular_dc(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient()
    instrument_dc_logging(client)  # type: ignore[arg-type]
    with caplog.at_level("INFO"):
        dc = await client._get_dc(4)
    assert dc.ip_address == "91.108.56.1"
    assert client.calls == [(4, False)]
    assert "Using DC 4 at 91.108.56.1:443" in caplog.text


async def test_logs_cdn_dc(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient()
    instrument_dc_logging(client)  # type: ignore[arg-type]
    with caplog.at_level("INFO"):
        await client._get_dc(203, cdn=True)
    assert client.calls == [(203, True)]
    assert "Using CDN DC 203 at 91.108.56.1:443" in caplog.text
