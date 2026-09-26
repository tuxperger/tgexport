"""Offline self-containment: no external resources in generated HTML (T047)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import aiosqlite

from tgexport.render.renderer import render_all
from tgexport.storage.models import RawAttachmentRef, RawDialog, RawMessage, RawSender
from tgexport.storage.repos.attachments import resolve_attachment, upsert_attachment_ref
from tgexport.storage.repos.chats import upsert_chat
from tgexport.storage.repos.messages import upsert_message


class ExternalResourceDetector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.external: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            external_prefixes = ("http://", "https://", "//")
            if name in ("src", "href") and value and value.startswith(external_prefixes):
                self.external.append(f"<{tag} {name}={value}>")


async def test_no_external_resources(db: aiosqlite.Connection, tmp_path: Path) -> None:
    await upsert_chat(db, RawDialog(100, "private", "Chat", None, None))
    await upsert_message(
        db,
        RawMessage(
            id=1,
            chat_id=100,
            sender=RawSender(1, "u", "U", None),
            date=datetime(2026, 7, 1, tzinfo=UTC),
            text="hello with a photo",
            attachments=(RawAttachmentRef("f1", "photo"),),
        ),
    )
    await upsert_attachment_ref(db, 1, 100, RawAttachmentRef("f1", "photo"))
    await resolve_attachment(db, "f1", "ff00", Path("media/100/ff/00_p.jpg"), None, None)

    output = tmp_path / "out"
    await render_all(db, output, tmp_path / "data" / "media")

    for html_file in output.rglob("*.html"):
        detector = ExternalResourceDetector()
        detector.feed(html_file.read_text())
        assert detector.external == [], f"{html_file.name} references external resources"


async def test_media_src_is_relative(db: aiosqlite.Connection, tmp_path: Path) -> None:
    await upsert_chat(db, RawDialog(100, "private", "Chat", None, None))
    await upsert_message(
        db,
        RawMessage(
            id=1,
            chat_id=100,
            sender=RawSender(1, None, "U", None),
            date=datetime(2026, 7, 1, tzinfo=UTC),
            text=None,
            attachments=(RawAttachmentRef("f1", "photo"),),
        ),
    )
    await upsert_attachment_ref(db, 1, 100, RawAttachmentRef("f1", "photo"))
    await resolve_attachment(db, "f1", "ff00", Path("media/100/ff/00_p.jpg"), None, None)

    output = tmp_path / "out"
    await render_all(db, output, tmp_path / "data" / "media")
    meta = json.loads((output / "100" / "data" / "meta.json").read_text())
    assert meta["media_prefix"].startswith("../"), "media prefix must climb out of the chat dir"
