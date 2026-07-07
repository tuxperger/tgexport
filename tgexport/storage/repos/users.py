"""User (sender snapshot) repository."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from tgexport.storage.models import RawSender
from tgexport.storage.upsert import upsert


async def upsert_user(conn: aiosqlite.Connection, sender: RawSender) -> None:
    if sender.id is None:
        return
    await upsert(
        conn,
        "users",
        {
            "id": sender.id,
            "username": sender.username,
            "first_name": sender.first_name,
            "last_name": sender.last_name,
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        conflict_cols=("id",),
    )
