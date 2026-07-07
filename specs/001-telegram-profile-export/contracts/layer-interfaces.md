# Contract: Layer Interfaces

**Date**: 2026-07-05

These are the Python-level module interfaces. They define what each layer exposes to
adjacent layers and what it is forbidden to import. Violations are caught by mypy and
enforced in code review.

---

## Import Rules (Constitution §IV)

```
cli        → fetch, storage, render, core
fetch      → core
storage    → core
render     → storage, core
live       → fetch, storage, core   [future; skeleton only]

render     ✗ fetch     (FORBIDDEN)
fetch      ✗ storage   (FORBIDDEN)
fetch      ✗ render    (FORBIDDEN)
storage    ✗ fetch     (FORBIDDEN)
storage    ✗ render    (FORBIDDEN)
```

---

## core/config.py

```python
@dataclass(frozen=True)
class Config:
    api_id:       int
    api_hash:     str
    session_path: Path
    db_path:      Path
    media_dir:    Path
    output_dir:   Path
    log_level:    str = "INFO"

def load_config(env_file: Path | None = None) -> Config:
    """Load from environment / .env file. Raises ConfigError on missing required vars."""
```

---

## fetch layer

### fetch/client.py

```python
class TelegramSession:
    """Async context manager wrapping TelegramClient."""
    async def __aenter__(self) -> TelegramClient: ...
    async def __aexit__(self, *_: object) -> None: ...

async def authenticate(cfg: Config) -> None:
    """Interactive login flow: phone → code → 2FA (if needed). Saves session."""
```

### fetch/dialogs.py

```python
@dataclass
class RawDialog:
    id:           int
    type:         Literal["private", "group", "supergroup", "channel"]
    title:        str
    username:     str | None
    member_count: int | None

async def iter_dialogs(client: TelegramClient) -> AsyncIterator[RawDialog]:
    """Yield all accessible dialogs. Handles pagination transparently."""
```

### fetch/messages.py

```python
@dataclass
class RawSender:
    id:         int | None
    username:   str | None
    first_name: str | None
    last_name:  str | None

@dataclass
class RawReaction:
    emoji: str
    count: int

@dataclass
class RawEdit:
    edited_at: datetime
    text:      str | None

@dataclass
class RawAttachmentRef:
    tg_file_id: str
    category:   Literal["photo","video","audio","voice","video_note","document","sticker","gif"]

@dataclass
class RawMessage:
    id:              int
    chat_id:         int
    sender:          RawSender | None
    date:            datetime
    text:            str | None
    reply_to_msg_id: int | None
    fwd_from_chat_id: int | None
    fwd_from_msg_id:  int | None
    fwd_from_name:   str | None
    service_type:    str | None
    is_deleted:      bool
    edits:           list[RawEdit]
    reactions:       list[RawReaction]
    attachments:     list[RawAttachmentRef]

async def iter_messages(
    client:      TelegramClient,
    chat_id:     int,
    min_id:      int = 0,          # For incremental sync: fetch only > min_id
    max_id:      int = 0,          # For resuming backward scan: fetch only < max_id
    batch_size:  int = 100,
) -> AsyncIterator[RawMessage]:
    """
    Yield messages. Direction depends on parameters:
    - min_id > 0: forward sync (newest first, stopping at min_id)
    - max_id > 0: backward scan resumption (continue from max_id downward)
    - neither: full backward scan from newest to oldest
    """
```

### fetch/media.py

```python
async def download_attachment(
    client:     TelegramClient,
    message:    RawMessage,
    att_ref:    RawAttachmentRef,
    dest_dir:   Path,
) -> tuple[Path, str]:
    """
    Download attachment to dest_dir. Returns (local_path, sha256_hex).
    Raises MediaDownloadError on failure after retries.
    Skips download if sha256 already exists in dest_dir (dedup check).
    """
```

### fetch/rate_limiter.py

```python
class RateLimiter:
    """
    Centralised rate limit enforcer.
    - download_semaphore: asyncio.Semaphore(3) for concurrent downloads
    - request_throttle: token bucket at 20 req/s
    Call acquire() before each Telegram API request.
    """
    async def acquire(self) -> None: ...
    async def acquire_download(self) -> AsyncContextManager[None]: ...

def with_retry(max_attempts: int = 5) -> Callable[[F], F]:
    """
    Decorator: catches FloodWaitError (sleep error.seconds + 1) and other
    RPCError / network errors (exponential backoff min(2^attempt, 60)s).
    """
```

---

## storage layer

### storage/db.py

```python
async def open_db(path: Path) -> aiosqlite.Connection:
    """Open connection; run pending migrations; set WAL + FK pragmas."""

async def run_migrations(conn: aiosqlite.Connection) -> None:
    """Apply any unapplied numbered migration files in storage/migrations/."""
```

### storage/repos/chats.py

```python
async def upsert_chat(conn: aiosqlite.Connection, chat: RawDialog) -> None: ...
async def get_all_chats(conn: aiosqlite.Connection) -> list[Chat]: ...
async def get_chat(conn: aiosqlite.Connection, chat_id: int) -> Chat | None: ...
```

### storage/repos/messages.py

```python
async def upsert_message(conn: aiosqlite.Connection, msg: RawMessage) -> None:
    """Upsert message, edits, and reactions atomically."""

async def get_messages(
    conn:      aiosqlite.Connection,
    chat_id:   int,
    offset:    int = 0,
    limit:     int = 500,
) -> list[Message]:
    """Return messages in chronological order for render pagination."""

async def get_message_count(conn: aiosqlite.Connection, chat_id: int) -> int: ...
```

### storage/repos/attachments.py

```python
async def upsert_attachment_ref(
    conn:    aiosqlite.Connection,
    msg_id:  int,
    chat_id: int,
    ref:     RawAttachmentRef,
) -> None:
    """Insert attachment row with sha256=NULL (pending download)."""

async def resolve_attachment(
    conn:       aiosqlite.Connection,
    tg_file_id: str,
    sha256:     str,
    local_path: Path,
    mime_type:  str | None,
    file_size:  int | None,
) -> None:
    """Update attachment with sha256 and upsert into files table."""

async def get_pending_attachments(
    conn: aiosqlite.Connection,
    chat_id: int | None = None,
) -> list[PendingAttachment]:
    """Return attachment rows where sha256 IS NULL (download not yet completed)."""
```

### storage/repos/sync_state.py

```python
async def get_sync_state(
    conn: aiosqlite.Connection,
    chat_id: int,
) -> SyncState | None: ...

async def update_watermark(
    conn:              aiosqlite.Connection,
    chat_id:           int,
    last_message_id:   int,
    oldest_message_id: int | None,
    history_complete:  bool,
) -> None: ...
```

---

## render layer

### render/renderer.py

```python
async def render_all(
    conn:      aiosqlite.Connection,
    output:    Path,
    page_size: int = 500,
    chat_ids:  list[int] | None = None,   # None = all chats
) -> None:
    """
    Generate output/index.html and output/<chat_id>/page_NNN.html.
    Reads exclusively from the database; no network access.
    """

async def render_chat(
    conn:      aiosqlite.Connection,
    chat_id:   int,
    output:    Path,
    page_size: int = 500,
) -> None:
    """Render (or re-render) a single chat. Called by live mode on update."""
```

---

## live layer (skeleton)

### live/daemon.py

```python
async def run_daemon(
    cfg:     Config,
    conn:    aiosqlite.Connection,
    output:  Path,
) -> None:
    """
    TODO (future): Register Telethon event handlers for NewMessage,
    MessageEdited, MessageDeleted. On each event:
      1. Call fetch to get the raw event data.
      2. Call storage.repos.messages.upsert_message().
      3. Call render.renderer.render_chat() for the affected chat.
    Runs indefinitely until interrupted.
    """
    raise NotImplementedError("Live mode is not implemented in this release.")
```

The daemon function signature is fixed so that `cli/cmd_watch.py` can call it without
changes when the live mode is implemented.
