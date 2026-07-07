"""Typed records mirroring the SQLite schema and raw fetch-layer data.

Raw* dataclasses are produced by the fetch layer and consumed by storage;
the plain dataclasses are read models returned by storage queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from tgexport.core.config import DialogType

AttachmentCategory = Literal[
    "photo", "video", "audio", "voice", "video_note", "document", "sticker", "gif"
]

ATTACHMENT_CATEGORIES: tuple[AttachmentCategory, ...] = (
    "photo",
    "video",
    "audio",
    "voice",
    "video_note",
    "document",
    "sticker",
    "gif",
)


# ── Raw records (fetch → storage) ────────────────────────────────────────────


@dataclass(frozen=True)
class RawDialog:
    id: int
    type: DialogType
    title: str
    username: str | None
    member_count: int | None


@dataclass(frozen=True)
class RawSender:
    id: int | None
    username: str | None
    first_name: str | None
    last_name: str | None

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        if parts:
            return " ".join(parts)
        return self.username or (f"id:{self.id}" if self.id else "Unknown")


@dataclass(frozen=True)
class RawReaction:
    emoji: str
    count: int


@dataclass(frozen=True)
class RawEdit:
    edited_at: datetime
    text: str | None


@dataclass(frozen=True)
class RawAttachmentRef:
    tg_file_id: str
    category: AttachmentCategory
    filename: str | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class RawMessage:
    id: int
    chat_id: int
    sender: RawSender | None
    date: datetime
    text: str | None
    reply_to_msg_id: int | None = None
    fwd_from_chat_id: int | None = None
    fwd_from_msg_id: int | None = None
    fwd_from_name: str | None = None
    service_type: str | None = None
    is_deleted: bool = False
    edits: tuple[RawEdit, ...] = field(default=())
    reactions: tuple[RawReaction, ...] = field(default=())
    attachments: tuple[RawAttachmentRef, ...] = field(default=())


# ── Read models (storage → render / cli) ─────────────────────────────────────


@dataclass(frozen=True)
class Chat:
    id: int
    type: DialogType
    title: str
    username: str | None
    member_count: int | None
    fetched_at: str


@dataclass(frozen=True)
class User:
    id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    fetched_at: str


@dataclass(frozen=True)
class EditRecord:
    message_id: int
    chat_id: int
    edited_at: str
    text: str | None


@dataclass(frozen=True)
class Reaction:
    message_id: int
    chat_id: int
    emoji: str
    count: int


@dataclass(frozen=True)
class FileRecord:
    sha256: str
    local_path: str
    mime_type: str | None
    file_size: int | None


@dataclass(frozen=True)
class Attachment:
    message_id: int
    chat_id: int
    tg_file_id: str
    sha256: str | None
    category: AttachmentCategory
    local_path: str | None = None


@dataclass(frozen=True)
class Message:
    id: int
    chat_id: int
    sender_id: int | None
    sender_name: str | None
    date: str
    text: str | None
    reply_to_msg_id: int | None
    fwd_from_chat_id: int | None
    fwd_from_msg_id: int | None
    fwd_from_name: str | None
    service_type: str | None
    is_deleted: bool
    edits: tuple[EditRecord, ...] = field(default=())
    reactions: tuple[Reaction, ...] = field(default=())
    attachments: tuple[Attachment, ...] = field(default=())


@dataclass(frozen=True)
class PendingAttachment:
    message_id: int
    chat_id: int
    tg_file_id: str
    category: AttachmentCategory


@dataclass(frozen=True)
class SyncState:
    chat_id: int
    last_message_id: int
    oldest_message_id: int | None
    history_complete: bool
    updated_at: str
