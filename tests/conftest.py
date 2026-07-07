"""Shared fixtures: fresh in-memory database per test."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiosqlite
import pytest

from tgexport.storage.db import open_db


@pytest.fixture
async def db() -> AsyncIterator[aiosqlite.Connection]:
    conn = await open_db(":memory:")
    yield conn
    await conn.close()
