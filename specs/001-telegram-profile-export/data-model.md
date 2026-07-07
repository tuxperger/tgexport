# Data Model: Telegram Profile Export

**Date**: 2026-07-05
**Branch**: `001-telegram-profile-export`

## Overview

The canonical data store is a single SQLite database. All HTML generation reads
exclusively from this database — the `render` layer never touches the `fetch` layer.
Attachment binaries live on disk under `media/`; the database records their paths
and SHA-256 hashes.

## Entity Relationship

```
chats ──< messages >── message_edits
            │
            ├──< attachments >── files
            └──< reactions

chats ──< sync_state (1:1)

messages >── users (sender snapshot)
```

## SQLite Schema (Authoritative DDL)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────
-- chats
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chats (
    id              INTEGER PRIMARY KEY,    -- Telegram chat ID (may be negative for groups)
    type            TEXT    NOT NULL        -- 'private' | 'group' | 'supergroup' | 'channel'
                    CHECK (type IN ('private', 'group', 'supergroup', 'channel')),
    title           TEXT    NOT NULL,
    username        TEXT,                   -- @handle, NULL if private/no username
    member_count    INTEGER,
    fetched_at      TEXT    NOT NULL        -- ISO8601 UTC timestamp
);

-- ─────────────────────────────────────────────
-- users  (sender identity snapshots)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,    -- Telegram user ID
    username        TEXT,
    first_name      TEXT,
    last_name       TEXT,
    fetched_at      TEXT    NOT NULL
);

-- ─────────────────────────────────────────────
-- messages
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER NOT NULL,       -- Telegram message ID (unique per chat)
    chat_id         INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    sender_id       INTEGER REFERENCES users(id),
    sender_name     TEXT,                   -- Display name snapshot at fetch time
    date            TEXT    NOT NULL,       -- ISO8601 UTC
    text            TEXT,                   -- Current text (NULL for media-only or service)
    reply_to_msg_id INTEGER,               -- NULL if not a reply
    fwd_from_chat_id INTEGER,              -- NULL if not forwarded
    fwd_from_msg_id  INTEGER,              -- NULL if not forwarded
    fwd_from_name   TEXT,                  -- Display name of original sender
    service_type    TEXT,                  -- NULL for normal messages; e.g. 'pinned', 'joined'
    is_deleted      INTEGER NOT NULL DEFAULT 0  -- 1 = marked deleted by Telegram
                    CHECK (is_deleted IN (0, 1)),
    fetched_at      TEXT    NOT NULL,
    PRIMARY KEY (id, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_date ON messages (chat_id, date);
CREATE INDEX IF NOT EXISTS idx_messages_reply     ON messages (chat_id, reply_to_msg_id)
    WHERE reply_to_msg_id IS NOT NULL;

-- ─────────────────────────────────────────────
-- message_edits  (full edit history)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS message_edits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL,
    chat_id         INTEGER NOT NULL,
    edited_at       TEXT    NOT NULL,       -- ISO8601 UTC; time of this edit
    text            TEXT,
    FOREIGN KEY (message_id, chat_id) REFERENCES messages(id, chat_id) ON DELETE CASCADE,
    UNIQUE (message_id, chat_id, edited_at)
);

-- ─────────────────────────────────────────────
-- files  (unique on-disk files, deduplicated by SHA-256)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS files (
    sha256          TEXT    PRIMARY KEY,    -- Hex SHA-256 of file content
    local_path      TEXT    NOT NULL,       -- Relative: media/<chat_id>/<prefix>/<name>
    mime_type       TEXT,
    file_size       INTEGER                 -- bytes
);

-- ─────────────────────────────────────────────
-- attachments  (message → file join; includes category)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS attachments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL,
    chat_id         INTEGER NOT NULL,
    tg_file_id      TEXT    NOT NULL,       -- Telegram file reference (for re-download if needed)
    sha256          TEXT    REFERENCES files(sha256),  -- NULL while download pending
    category        TEXT    NOT NULL
                    CHECK (category IN (
                        'photo', 'video', 'audio', 'voice',
                        'video_note', 'document', 'sticker', 'gif'
                    )),
    FOREIGN KEY (message_id, chat_id) REFERENCES messages(id, chat_id) ON DELETE CASCADE,
    UNIQUE (message_id, chat_id, tg_file_id)
);

CREATE INDEX IF NOT EXISTS idx_attachments_sha256 ON attachments (sha256);

-- ─────────────────────────────────────────────
-- reactions
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL,
    chat_id         INTEGER NOT NULL,
    emoji           TEXT    NOT NULL,
    count           INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (message_id, chat_id) REFERENCES messages(id, chat_id) ON DELETE CASCADE,
    UNIQUE (message_id, chat_id, emoji)
);

-- ─────────────────────────────────────────────
-- sync_state  (per-chat watermark; 1-to-1 with chats)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sync_state (
    chat_id             INTEGER PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
    last_message_id     INTEGER NOT NULL DEFAULT 0, -- Highest fetched msg ID (for incremental sync)
    oldest_message_id   INTEGER,                    -- Lowest fetched msg ID (for resuming initial scan)
    history_complete    INTEGER NOT NULL DEFAULT 0  -- 1 = full history fetched
                        CHECK (history_complete IN (0, 1)),
    updated_at          TEXT    NOT NULL
);
```

## Entities

### Chat

Fields: `id`, `type` (`private` | `group` | `supergroup` | `channel`), `title`,
`username` (nullable), `member_count` (nullable), `fetched_at`.

Identity: Telegram chat ID (globally unique per account).

State transitions: Updated on each sync run (metadata snapshot).

### User

Fields: `id`, `username`, `first_name`, `last_name`, `fetched_at`.

Identity: Telegram user ID. Stored as snapshot; may drift from current Telegram profile.
If the sender is anonymous (e.g., channel post), `sender_id` in `messages` is NULL and
`sender_name` holds the display name.

### Message

Fields: `id` + `chat_id` (composite PK), `sender_id`, `sender_name`, `date`, `text`,
`reply_to_msg_id`, `fwd_from_*`, `service_type`, `is_deleted`, `fetched_at`.

Identity: `(id, chat_id)` pair — Telegram message IDs are unique per chat, not globally.

State transitions:
- Normal: `is_deleted = 0`, `text` contains latest text.
- Edited: `is_deleted = 0`; edit history in `message_edits`. `text` always holds latest.
- Deleted: `is_deleted = 1`; `text` may be NULL (content not recoverable if not already stored).

### Attachment / File

Two-table design for deduplication:

- `files`: one row per unique file on disk, keyed by SHA-256.
- `attachments`: one row per message-attachment link; holds `category` and `tg_file_id`;
  references `files.sha256` once downloaded.

Download state: `sha256` is NULL in `attachments` until the file is successfully
downloaded and hash computed. This enables re-downloading failed attachments on next run.

File layout on disk:
```
media/
└── <chat_id>/
    └── <sha256[:2]>/
        └── <sha256[2:]>_<sanitised_original_name>
```

The two-character prefix sharding avoids inode-limit issues in large chats.

### EditRecord

Fields: `message_id`, `chat_id`, `edited_at` (unique per message+time), `text`.

Append-only. Unique constraint on `(message_id, chat_id, edited_at)` ensures idempotent
upsert even if the same edit is fetched multiple times.

### Reaction

Fields: `message_id`, `chat_id`, `emoji`, `count`.

Unique on `(message_id, chat_id, emoji)`. On re-fetch the count is updated via
`INSERT OR REPLACE`.

### SyncState

Fields: `chat_id`, `last_message_id`, `oldest_message_id`, `history_complete`,
`updated_at`.

Semantics:
- `history_complete = 0`: initial backward scan in progress; resume from
  `oldest_message_id` going further back.
- `history_complete = 1`: full history is stored; use `last_message_id` as
  `min_id` parameter in `iter_messages` for incremental forward sync.

Watermark is committed every 200 messages (configurable) to balance crash durability
against write amplification.

## Validation Rules

- `chat.type` is one of the four enumerated values (enforced by CHECK constraint).
- `message.is_deleted` is 0 or 1 (CHECK constraint).
- `attachment.category` is one of the eight enumerated values (CHECK constraint).
- `sync_state.history_complete` is 0 or 1 (CHECK constraint).
- Foreign key constraints are enabled at connection time (`PRAGMA foreign_keys = ON`).
- All timestamps are stored as ISO8601 UTC strings (`2026-07-05T14:30:00Z`).

## Migration Strategy

Migrations are numbered SQL files: `storage/migrations/0001_initial.sql`,
`0002_add_reactions.sql`, etc. The `db.py` module tracks applied migrations in a
`_migrations` meta-table and runs any unapplied ones at startup in transaction.
This enables safe schema evolution for the live-mode extension (e.g., adding an
`events` table) without breaking existing databases.
