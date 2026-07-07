# Research: Telegram Profile Export

**Date**: 2026-07-05
**Branch**: `001-telegram-profile-export`

## Decision Log

### 1. MTProto Client Library

**Decision**: Telethon 1.x + cryptg

**Rationale**: Telethon is the de-facto Python user-account MTProto client. It is
actively maintained, fully asyncio-native, covers the full API surface needed
(iter_dialogs, iter_messages, download_media, event handlers for live mode), and
uses native session file management. `cryptg` provides C-based crypto acceleration
that significantly reduces CPU usage when downloading large media files.

**Alternatives considered**:
- `pyrogram`: also capable, but Telethon has a more stable async API and broader
  community documentation for user-account (non-bot) access patterns.
- `tdlib` (python-tdlib): C++ backend, harder to integrate in Nix, overkill.

---

### 2. Async Database Access

**Decision**: `aiosqlite` wrapping the standard `sqlite3` module

**Rationale**: `aiosqlite` runs SQLite in a thread pool behind an asyncio interface,
allowing the event loop to remain unblocked during database writes. For this tool's
workload (single writer, moderate throughput) this is sufficient. Pure `sqlite3` in a
single asyncio thread would block the event loop during large upsert batches.

**Alternatives considered**:
- `databases` library: adds an abstraction layer but SQLite dialect is limited and adds
  unnecessary complexity.
- Async ORM (SQLAlchemy asyncio): too much abstraction for a personal tool with a fixed
  schema.

---

### 3. HTML Templating

**Decision**: Jinja2

**Rationale**: Jinja2 is the Python standard for offline template rendering. It supports
template inheritance (base layout → chat page), custom filters (timestamp formatting,
media type detection), and conditional blocks for optional sections (reactions,
edit history). It has no runtime dependencies beyond the Jinja2 package itself.

**Alternatives considered**:
- `mako`: comparable capability, less familiar ecosystem.
- String formatting / `dominate`: too fragile for complex HTML with nesting.

---

### 4. CLI Framework

**Decision**: `click` (via Telethon's transitive dep or declared separately)

**Rationale**: `click` provides clean subcommand grouping (`@click.group`), automatic
`--help` generation, and composable option/argument decoration. It integrates cleanly
with asyncio via `asyncio.run()` wrappers. The subcommand structure (`login`, `export`,
`render`, `watch`) maps directly onto `click.group` + `click.command`.

**Alternatives considered**:
- `argparse`: more verbose, manual subcommand wiring.
- `typer`: Pydantic-based, adds dependency weight; click is lighter for this use case.

---

### 5. Rate Limiting Strategy

**Decision**: Layered approach in `fetch/rate_limiter.py`

**Rationale**: Telegram's MTProto API enforces per-method and global flood limits.
Violating them risks account restrictions (up to temporary bans). The strategy:

1. **FloodWaitError handler**: catch `telethon.errors.FloodWaitError`, sleep
   `error.seconds + 1`, log at WARNING, retry the same call.
2. **Exponential back-off decorator**: for `RPCError` / network errors, retry up to 5
   times with `sleep(min(2 ** attempt, 60))`.
3. **Download semaphore**: `asyncio.Semaphore(3)` — at most 3 concurrent file downloads.
4. **Global request throttle**: token-bucket at 20 req/s using an asyncio-compatible
   `asyncio.Lock` + timestamp tracking (or `aiolimiter` package if available in flake).

All fetch callsites MUST be wrapped with the rate limiter; there is no opt-out.

---

### 6. Watermark / Incremental Sync Design

**Decision**: Dual-cursor watermark in `sync_state` table

**Rationale**: Telegram's `iter_messages` iterates from newest to oldest by default.
During initial history fetch we go backward. We need two cursors:

- `oldest_fetched_id`: the lowest message ID we have successfully stored. While
  `history_complete = 0`, we resume the backward scan from this ID.
- `last_message_id`: the highest (newest) message ID stored. During incremental sync
  (`history_complete = 1`), we call `iter_messages(min_id=last_message_id)` to get
  only newer messages.

The watermark is committed **per batch** (e.g., every 200 messages), not per message,
to balance durability and write amplification.

---

### 7. Attachment Deduplication

**Decision**: SHA-256 hash as deduplication key in a `files` table; `attachments`
table references it via FK.

**Rationale**: The same photo or document may be forwarded across multiple chats or
re-sent in the same chat. Storing one file per unique SHA-256 and linking multiple
message-attachment rows to it keeps disk usage minimal.

**File layout**: `media/<chat_id>/<sha256[:2]>/<sha256[2:]>_<original_filename>`
This two-level prefix sharding avoids single-directory inode limits for large chats.

---

### 8. HTML Pagination

**Decision**: Paginate per-chat HTML at 500 messages per page; generate
`<chat_id>/page_001.html`, `<chat_id>/page_002.html`, …, with prev/next links.

**Rationale**: Chats with tens of thousands of messages would produce multi-MB HTML
files that browsers struggle to render. 500 messages per page keeps each file under
~1–2 MB. The index page links to `page_001.html` for each chat.

**Alternatives considered**:
- Single file per chat: simpler but unusable for large chats (100k+ messages).
- Infinite scroll with JS: contradicts the "static offline" requirement.

---

### 9. Secret / Config Management

**Decision**: python-dotenv reads `.env` file; Settings loaded into a typed `Config`
dataclass; validated at startup.

**Variables**:
```
TG_API_ID=<int>
TG_API_HASH=<str>
TG_SESSION_PATH=<path>      # default: ./data/session.session
TG_DB_PATH=<path>           # default: ./data/archive.db
TG_MEDIA_DIR=<path>         # default: ./data/media
TG_OUTPUT_DIR=<path>        # default: ./output
```

`.env` file MUST have permissions 600. `.gitignore` MUST list `.env`, `*.session`,
`*.db`, `data/`, `output/`.

---

### 10. Testing Strategy

**Decision**: Unit tests only for `storage` and `render` layers; `fetch` is excluded
(requires live Telegram session); `live` has no logic yet.

- `storage` tests: in-memory SQLite (`aiosqlite` with `:memory:`); test all repo
  upsert paths, migration runner, deduplication logic.
- `render` tests: inject fixture `Chat` / `Message` objects; assert HTML output
  contains expected strings; no disk I/O needed.
- Test isolation: `@pytest.fixture` with fresh in-memory DB per test; no global state.
- CI gate: `mypy --strict`, `ruff check`, `pytest` must all pass.
