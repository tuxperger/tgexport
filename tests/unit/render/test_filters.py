"""Jinja2 filter behaviour (T031)."""

from __future__ import annotations

from tgexport.render.filters import format_datetime, media_icon, truncate_text


def test_format_datetime_iso() -> None:
    assert format_datetime("2026-07-01T12:30:00Z") == "2026-07-01 12:30"


def test_format_datetime_garbage_passthrough() -> None:
    assert format_datetime("not-a-date") == "not-a-date"


def test_media_icon_known_categories() -> None:
    categories = ("photo", "video", "audio", "voice", "video_note", "document", "sticker", "gif")
    for category in categories:
        assert media_icon(category) != "📎", f"missing icon for {category}"


def test_media_icon_unknown_fallback() -> None:
    assert media_icon("weird") == "📎"


def test_truncate_short_text_unchanged() -> None:
    assert truncate_text("hello") == "hello"


def test_truncate_long_text() -> None:
    result = truncate_text("x" * 200, 120)
    assert len(result) <= 120
    assert result.endswith("…")


def test_truncate_none() -> None:
    assert truncate_text(None) == ""
