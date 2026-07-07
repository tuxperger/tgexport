"""Unit tests for call_with_retry description handling."""

from __future__ import annotations

import pytest

from tgexport.fetch import rate_limiter
from tgexport.fetch.rate_limiter import call_with_retry


@pytest.fixture(autouse=True)
def instant_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr(rate_limiter.asyncio, "sleep", _no_sleep)


async def test_callable_description_evaluated_at_log_time(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = "149.154.167.51:443"
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("Cannot send requests while disconnected")
        return "ok"

    with caplog.at_level("WARNING"):
        result = await call_with_retry(flaky, description=lambda: f"download (server: {server})")
    assert result == "ok"
    assert "download (server: 149.154.167.51:443) failed (attempt 1/5)" in caplog.text


async def test_plain_string_description_still_works(caplog: pytest.LogCaptureFixture) -> None:
    async def failing() -> None:
        raise ConnectionError("boom")

    with caplog.at_level("WARNING"):
        with pytest.raises(ConnectionError):
            await call_with_retry(failing, max_attempts=2, description="fetch messages")
    assert "fetch messages failed (attempt 1/2)" in caplog.text
    assert "fetch messages failed after 2 attempts" in caplog.text
