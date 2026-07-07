-- Initial schema. See specs/001-telegram-profile-export/data-model.md.

CREATE TABLE IF NOT EXISTS chats (
    id              INTEGER PRIMARY KEY,
    type            TEXT    NOT NULL
                    CHECK (type IN ('private', 'group', 'supergroup', 'channel')),
    title           TEXT    NOT NULL,
    username        TEXT,
    member_count    INTEGER,
    fetched_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,
    username        TEXT,
    first_name      TEXT,
    last_name       TEXT,
    fetched_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER NOT NULL,
    chat_id         INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    sender_id       INTEGER REFERENCES users(id),
    sender_name     TEXT,
    date            TEXT    NOT NULL,
    text            TEXT,
    reply_to_msg_id INTEGER,
    fwd_from_chat_id INTEGER,
    fwd_from_msg_id  INTEGER,
    fwd_from_name   TEXT,
    service_type    TEXT,
    is_deleted      INTEGER NOT NULL DEFAULT 0
                    CHECK (is_deleted IN (0, 1)),
    fetched_at      TEXT    NOT NULL,
    PRIMARY KEY (id, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_date ON messages (chat_id, date);
CREATE INDEX IF NOT EXISTS idx_messages_reply     ON messages (chat_id, reply_to_msg_id)
    WHERE reply_to_msg_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS message_edits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL,
    chat_id         INTEGER NOT NULL,
    edited_at       TEXT    NOT NULL,
    text            TEXT,
    FOREIGN KEY (message_id, chat_id) REFERENCES messages(id, chat_id) ON DELETE CASCADE,
    UNIQUE (message_id, chat_id, edited_at)
);

CREATE TABLE IF NOT EXISTS files (
    sha256          TEXT    PRIMARY KEY,
    local_path      TEXT    NOT NULL,
    mime_type       TEXT,
    file_size       INTEGER
);

CREATE TABLE IF NOT EXISTS attachments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL,
    chat_id         INTEGER NOT NULL,
    tg_file_id      TEXT    NOT NULL,
    sha256          TEXT    REFERENCES files(sha256),
    category        TEXT    NOT NULL
                    CHECK (category IN (
                        'photo', 'video', 'audio', 'voice',
                        'video_note', 'document', 'sticker', 'gif'
                    )),
    FOREIGN KEY (message_id, chat_id) REFERENCES messages(id, chat_id) ON DELETE CASCADE,
    UNIQUE (message_id, chat_id, tg_file_id)
);

CREATE INDEX IF NOT EXISTS idx_attachments_sha256 ON attachments (sha256);

CREATE TABLE IF NOT EXISTS reactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL,
    chat_id         INTEGER NOT NULL,
    emoji           TEXT    NOT NULL,
    count           INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (message_id, chat_id) REFERENCES messages(id, chat_id) ON DELETE CASCADE,
    UNIQUE (message_id, chat_id, emoji)
);

CREATE TABLE IF NOT EXISTS sync_state (
    chat_id             INTEGER PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
    last_message_id     INTEGER NOT NULL DEFAULT 0,
    oldest_message_id   INTEGER,
    history_complete    INTEGER NOT NULL DEFAULT 0
                        CHECK (history_complete IN (0, 1)),
    updated_at          TEXT    NOT NULL
);
