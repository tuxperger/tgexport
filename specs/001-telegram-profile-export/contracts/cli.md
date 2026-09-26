# Contract: CLI Interface

**Date**: 2026-07-05
**Entry point**: `tgexport` (installed via `pyproject.toml` scripts)

All commands are async internally. The CLI layer wraps each command in `asyncio.run()`.
Configuration is read from environment variables or a `.env` file in the working
directory.

---

## Global Options

```
tgexport [--env FILE] [--data-dir DIR] [--output-dir DIR] COMMAND [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--env FILE` | `.env` | Path to env file (python-dotenv) |
| `--data-dir DIR` | `./data` | Directory for session, DB, and media |
| `--output-dir DIR` | `./output` | Directory for generated HTML |

---

## Commands

### `tgexport login`

Authenticate and save session. Interactive: prompts for phone number, then for the
Telegram confirmation code, then (if 2FA is enabled) for the 2FA password.
Exits successfully once the session file is written.

```
tgexport login [--session-path FILE]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--session-path FILE` | `$TG_SESSION_PATH` or `data/session.session` | Where to write/read the session |

**Exit codes**: 0 = success, 1 = auth failure, 2 = config error.

**Output**: Progress to stdout; session file created with permissions 600.

---

### `tgexport export`

Fetch (or incrementally update) message history and attachments for all (or selected)
dialogs. Automatically authenticates if no session exists (same flow as `login`).
After fetching, triggers a full render pass.

```
tgexport export
    [--include ID_OR_NAME [ID_OR_NAME …]]
    [--exclude ID_OR_NAME [ID_OR_NAME …]]
    [--type  {private,group,supergroup,channel} […]]
    [--no-media]
    [--no-render]
    [--batch-size N]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--include` | (all) | Whitelist: chat IDs (integers) or exact titles |
| `--exclude` | (none) | Blacklist: chat IDs or exact titles |
| `--type` | (all types) | Restrict to given dialog type(s) |
| `--no-media` | off | Skip attachment downloads |
| `--no-render` | off | Skip HTML generation after fetch |
| `--batch-size N` | 200 | Messages committed per watermark update |

**Behaviour**:
- If `sync_state.history_complete = 0` for a chat: resume backward scan.
- If `sync_state.history_complete = 1`: fetch only messages newer than
  `last_message_id`.
- All writes are idempotent; safe to Ctrl-C and restart.

**Exit codes**: 0 = success, 1 = Telegram error (not recoverable after retries),
2 = config error.

**Output**: rich progress bars per dialog; summary table at end.

---

### `tgexport render`

Generate (or regenerate) the HTML archive from the local database. Requires no
network access.

```
tgexport render
    [--include ID […]]
    [--chunk-size N]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--include` | (all) | Limit render to specified chat IDs |
| `--chunk-size N` | 1000 | Messages per JSON data chunk |

**Output**:

```
output/index.html                       chat list + global search
output/chats.json                       [{id, title}]
output/<chat_id>/index.html             single viewer page per chat
output/<chat_id>/data/meta.json         chat info, media prefix, chunk directory
output/<chat_id>/data/chunk_NNNN.json   messages (chronological, N per chunk)
output/<chat_id>/data/search.json       [id, chunk, sender, date, text] tuples
```

The viewer opens at the newest messages and loads neighbouring chunks while
scrolling; `#msg-<id>` links load the chunk containing that message. Browsers
refuse fetch() over file://, so view the archive via `tgexport serve`.

**Exit codes**: 0 = success, 1 = DB error, 2 = config error.

---

### `tgexport serve`

```
tgexport serve [--host 127.0.0.1] [--port 8000]
```

Serves the archive over loopback HTTP. Only the output and media directories are
reachable; the session file and database return 404.

---

### `tgexport dataset`

Build an LLM fine-tuning dataset of channel posts, to teach a model to write posts
in the channels' style. Offline, reads the local database only.

```
tgexport dataset
    [--out DIR]             default: <data dir>/dataset
    [--include ID […]] [--type TYPE […]]      default type: channel
    [--format chat|text]    chat (default) or raw text
    [--system TEXT]         system prompt, "{chat}" → channel title ("" to omit)
    [--prompt TEXT]         user instruction before each post
    [--min-chars N]         40 — shorter posts are skipped
    [--include-forwards]    keep reposts from other channels
    [--val-ratio F] [--seed N]
```

**Output**: `train.jsonl` and `val.jsonl`, one post per line:

- `chat`: `{"messages": [system, {"role": "user", "content": <prompt>}, {"role": "assistant", "content": <post>}]}`
- `text`: `{"text": <post>}`

Post text keeps Telethon's markdown (bold, links). Service messages, caption-less
album items, short posts, reposts and exact duplicates are skipped.

**Exit codes**: 0 = success, 1 = DB missing.

---

### `tgexport watch` *(stub — future live mode)*

```
tgexport watch
```

Currently raises `NotImplementedError` with a message explaining the feature is
planned. The command is registered so that the CLI entry point is stable and scripts
that call it can detect unsupported status via exit code 3.

**Exit codes**: 3 = not implemented.

---

## Environment Variables

All variables are optional if the corresponding CLI flag or default applies.

```
TG_API_ID          <int>    Telegram app API ID (required)
TG_API_HASH        <str>    Telegram app API hash (required)
TG_SESSION_PATH    <path>   Session file path (default: data/session.session)
TG_DB_PATH         <path>   SQLite database path (default: data/archive.db)
TG_MEDIA_DIR       <path>   Attachment storage root (default: data/media)
TG_OUTPUT_DIR      <path>   HTML output root (default: output)
TG_LOG_LEVEL       <str>    Logging level: DEBUG|INFO|WARNING|ERROR (default: INFO)
```

`TG_API_ID` and `TG_API_HASH` are required for any command that connects to Telegram.
`tgexport render` does not require them.

---

## .env.example

```dotenv
TG_API_ID=12345678
TG_API_HASH=0123456789abcdef0123456789abcdef
TG_SESSION_PATH=data/session.session
TG_DB_PATH=data/archive.db
TG_MEDIA_DIR=data/media
TG_OUTPUT_DIR=output
TG_LOG_LEVEL=INFO
```
