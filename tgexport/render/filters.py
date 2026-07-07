"""Custom Jinja2 filters for the HTML templates."""

from __future__ import annotations

from datetime import datetime

MEDIA_ICONS = {
    "photo": "🖼",
    "video": "🎬",
    "audio": "🎵",
    "voice": "🎤",
    "video_note": "⭕",
    "document": "📄",
    "sticker": "🩹",
    "gif": "🎞",
}


def format_datetime(iso_utc: str) -> str:
    """Render an ISO8601 UTC timestamp as a readable string."""
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return iso_utc
    return dt.strftime("%Y-%m-%d %H:%M")


def media_icon(category: str) -> str:
    return MEDIA_ICONS.get(category, "📎")


def truncate_text(text: str | None, length: int = 120) -> str:
    if not text:
        return ""
    if len(text) <= length:
        return text
    return text[: length - 1].rstrip() + "…"
