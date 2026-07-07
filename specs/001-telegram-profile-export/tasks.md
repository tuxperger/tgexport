---

description: "Task list for Telegram Profile Export"
---

# Tasks: Telegram Profile Export

**Input**: Design documents from `specs/001-telegram-profile-export/`

**Prerequisites**: plan.md ✅ · spec.md ✅ · research.md ✅ · data-model.md ✅ · contracts/ ✅

**Tests**: Storage and render layers use TDD (tests written first). Fetch and CLI layers
are excluded from unit tests (require live Telegram session).

**Organization**: Phases 1–2 are shared foundation. Phases 3–7 map to user stories.
Phase 8 is live-mode skeleton (non-blocking). Phase 9 is polish.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies on in-progress tasks)
- **[Story]**: Which user story this task belongs to (US1–US5)

---

## Phase 1: Setup

**Purpose**: Project skeleton and tooling configuration. No story dependency.

- [X] T001 Create source directory tree: `tgexport/core/`, `tgexport/fetch/`, `tgexport/storage/repos/`, `tgexport/storage/migrations/`, `tgexport/render/templates/`, `tgexport/live/`, `tgexport/cli/`, `tests/unit/storage/`, `tests/unit/render/`
- [X] T002 Create `pyproject.toml` with package name `tgexport`, entry point `tgexport = "tgexport.cli.main:cli"`, and `[tool.mypy]` (strict=true), `[tool.ruff]` (select=["E","F","I","UP","ANN"]), `[tool.pytest.ini_options]` (asyncio_mode="auto") sections
- [X] T003 [P] Create `flake.nix` declaring Python 3.12 environment with: telethon, cryptg, aiosqlite, jinja2, click, rich, python-dotenv, pytest, pytest-asyncio, mypy, ruff as packages
- [X] T004 [P] Create `.gitignore` listing: `.env`, `*.session`, `*.db`, `data/`, `output/`, `__pycache__/`, `.mypy_cache/`, `.ruff_cache/`, `*.egg-info/`
- [X] T005 [P] Create `.env.example` with all seven `TG_*` variables (TG_API_ID, TG_API_HASH, TG_SESSION_PATH, TG_DB_PATH, TG_MEDIA_DIR, TG_OUTPUT_DIR, TG_LOG_LEVEL) and inline comments; add permission note (`chmod 600 .env`)

**Checkpoint**: `nix develop` drops into shell with all tools available; `tgexport --help` fails gracefully (package not installed yet).

---

## Phase 2: Foundation

**Purpose**: Core infrastructure that blocks ALL user stories.
All tasks here must complete before any user-story work begins.

**⚠️ CRITICAL**: No user-story work starts until Phase 2 is complete.

- [X] T006 Create `tgexport/core/config.py`: frozen `Config` dataclass (api_id: int, api_hash: str, session_path: Path, db_path: Path, media_dir: Path, output_dir: Path, log_level: str = "INFO"); `load_config(env_file: Path | None) -> Config` reads from env/dotenv, raises `ConfigError` with clear message when TG_API_ID or TG_API_HASH is absent
- [X] T007 Create `tgexport/storage/migrations/0001_initial.sql` with the full DDL from data-model.md: tables `chats`, `users`, `messages`, `message_edits`, `files`, `attachments`, `reactions`, `sync_state`; PRAGMA WAL; FK ON; all CHECK constraints; all indexes
- [X] T008 Create `tgexport/storage/db.py`: `open_db(path: Path) -> aiosqlite.Connection` (sets WAL + FK pragmas, runs migrations); `run_migrations(conn)` tracks applied migrations in `_schema_migrations` meta-table and applies any unapplied SQL files from `storage/migrations/` in order
- [X] T009 Create `tgexport/storage/models.py` with typed dataclasses matching data-model.md entities: `Chat`, `User`, `Message`, `EditRecord`, `Reaction`, `FileRecord`, `Attachment`, `SyncState`, `PendingAttachment`; all fields typed; no optional fields without explicit `| None`
- [X] T010 [P] Create `tgexport/storage/upsert.py`: helper `upsert(conn, table, data, conflict_cols)` implementing `INSERT INTO … ON CONFLICT(…) DO UPDATE SET …`; used by all repo modules for idempotent writes
- [X] T011 [P] Create `tgexport/fetch/rate_limiter.py`: `RateLimiter` class with `asyncio.Semaphore(3)` for download concurrency and token-bucket throttle at 20 req/s; `acquire() -> None`; `acquire_download()` async context manager; `with_retry(max_attempts=5)` decorator catching `FloodWaitError` (sleep `error.seconds + 1`, log WARNING) and other transient errors (exponential back-off `min(2**attempt, 60)` seconds)
- [X] T012 Create `tgexport/fetch/client.py`: `TelegramSession` async context manager that initialises `TelegramClient(session_path, api_id, api_hash)` and yields connected client; `authenticate(cfg: Config) -> None` interactive login flow (phone → SMS code → optional 2FA password) saving session file with `chmod 600`
- [X] T013 Create `tgexport/cli/main.py`: `@click.group()` function `cli` with `--env`, `--data-dir`, `--output-dir` options; loads `Config` via `load_config`; passes config via `click.pass_obj`; registers all subcommands; sets up `rich` logging handler
- [X] T014 Create `tgexport/cli/cmd_login.py`: `@cli.command("login")` with `--session-path` option; calls `authenticate(cfg)`; prints success and session path on completion; exits 2 on `ConfigError`, 1 on auth failure
- [X] T015 [P] Write `tests/unit/storage/test_migrations.py`: test that `open_db(":memory:")` runs `0001_initial.sql` successfully; test that re-running migrations is idempotent (no error, no duplicate rows in `_schema_migrations`); test that schema has all required tables
- [X] T016 [P] Write `tests/unit/storage/test_db.py`: test that `open_db` returns connection with `journal_mode=WAL` and `foreign_keys=ON`; test that `ConfigError` is raised with a descriptive message when required env vars are absent from a temp env

**Checkpoint**: `mypy --strict tgexport/core/ tgexport/storage/db.py tgexport/storage/models.py tgexport/fetch/rate_limiter.py tgexport/fetch/client.py` passes; `pytest tests/unit/storage/test_migrations.py tests/unit/storage/test_db.py` passes; `tgexport login --help` is visible.

---

## Phase 3: User Story 1 — Full Initial Export (Priority: P1) 🎯 MVP

**Goal**: Authenticate, enumerate all dialogs, fetch complete history, download all
attachments, store everything in SQLite, generate static HTML archive.

**Independent Test**: Run against a real account with ≥1 personal chat, ≥1 group, ≥1 channel.
Verify `data/archive.db` is non-empty, `output/index.html` lists all chats, all pages
open offline with inline media.

### Storage Tests (write first — must FAIL before implementation)

- [X] T017 [P] [US1] Write `tests/unit/storage/test_repos_chats.py`: test `upsert_chat` creates row; test repeated upsert updates `fetched_at` without duplicating; test `get_all_chats` returns typed `Chat` list; use in-memory SQLite fixture
- [X] T018 [P] [US1] Write `tests/unit/storage/test_repos_users.py`: test `upsert_user` idempotency; test NULL fields are accepted
- [X] T019 [P] [US1] Write `tests/unit/storage/test_repos_messages.py`: test `upsert_message` with edits and reactions in a single call; test repeated upsert does not duplicate edits (unique constraint on `edited_at`); test reaction count update via ON CONFLICT
- [X] T020 [P] [US1] Write `tests/unit/storage/test_repos_attachments.py`: test `upsert_attachment_ref` creates row with `sha256=NULL`; test `resolve_attachment` writes to `files` and updates `attachments.sha256`; test that two messages referencing same SHA-256 share one `files` row (dedup)
- [X] T021 [P] [US1] Write `tests/unit/storage/test_repos_sync_state.py`: test `get_sync_state` returns None for unknown chat; test `update_watermark` creates then updates row; test `history_complete` flag transitions 0→1

### Storage Implementation

- [X] T022 [P] [US1] Create `tgexport/storage/repos/chats.py`: `upsert_chat(conn, dialog: RawDialog) -> None`; `get_all_chats(conn) -> list[Chat]`; `get_chat(conn, chat_id: int) -> Chat | None`
- [X] T023 [P] [US1] Create `tgexport/storage/repos/users.py`: `upsert_user(conn, sender: RawSender) -> None`
- [X] T024 [US1] Create `tgexport/storage/repos/messages.py`: `upsert_message(conn, msg: RawMessage) -> None` atomically upserts base row, all `EditRecord`s (INSERT OR IGNORE on unique `edited_at`), and all `Reaction`s (ON CONFLICT DO UPDATE count); `get_messages(conn, chat_id, offset, limit) -> list[Message]`; `get_message_count(conn, chat_id) -> int`
- [X] T025 [P] [US1] Create `tgexport/storage/repos/attachments.py`: `upsert_attachment_ref(conn, msg_id, chat_id, ref: RawAttachmentRef) -> None` (sha256=NULL); `resolve_attachment(conn, tg_file_id, sha256, local_path, mime_type, file_size) -> None`; `get_pending_attachments(conn, chat_id=None) -> list[PendingAttachment]`
- [X] T026 [P] [US1] Create `tgexport/storage/repos/sync_state.py`: `get_sync_state(conn, chat_id) -> SyncState | None`; `update_watermark(conn, chat_id, last_message_id, oldest_message_id, history_complete) -> None`

### Fetch Implementation

- [X] T027 [P] [US1] Create `tgexport/fetch/dialogs.py`: `iter_dialogs(client, rate_limiter) -> AsyncIterator[RawDialog]` — wraps `client.iter_dialogs()`, maps each entity to `RawDialog` with correct `type` literal, applies rate limiter
- [X] T028 [US1] Create `tgexport/fetch/messages.py`: `iter_messages(client, rate_limiter, chat_id, min_id=0, max_id=0, batch_size=100) -> AsyncIterator[RawMessage]` — iterates `client.iter_messages(entity, min_id=min_id, max_id=max_id, limit=batch_size)` in pages; maps Telethon `Message` to `RawMessage` including `RawSender`, `RawEdit` list (from `edit_date`), `RawReaction` list, `RawAttachmentRef` list; handles service messages; applies rate limiter per page request
- [X] T029 [US1] Create `tgexport/fetch/media.py`: `download_attachment(client, rate_limiter, msg, att_ref: RawAttachmentRef, dest_dir: Path, conn) -> tuple[Path, str] | None` — checks `files` table for existing SHA-256 before downloading; downloads to temp file; computes SHA-256; moves to `media/<chat_id>/<sha256[:2]>/<sha256[2:]>_<name>`; sets file permissions 600; returns `(local_path, sha256)` or None on skip

### Render Tests (write first — must FAIL before implementation)

- [X] T030 [P] [US1] Write `tests/unit/render/test_renderer.py`: fixture creates in-memory DB with one chat, three messages (one with attachment ref, one reply, one forward); test `render_all(conn, tmp_path)` creates `output/index.html` and `output/<chat_id>/page_001.html`; test both files contain chat title and sender names; test page file links are relative
- [X] T031 [P] [US1] Write `tests/unit/render/test_filters.py`: test `format_datetime` filter outputs ISO-formatted local string; test `media_icon` filter returns correct icon string per category; test `truncate` filter (for index page preview)

### Render Implementation

- [X] T032 [US1] Create `tgexport/render/filters.py`: Jinja2 custom filters: `format_datetime(dt_str) -> str` (UTC → readable local); `media_icon(category) -> str`; `truncate_text(s, n) -> str`; register all in `Environment`
- [X] T033 [US1] Create `tgexport/render/templates/base.html.j2`: HTML5 skeleton; inline CSS (no CDN, no external fonts); defines blocks: `title`, `content`, `nav`; navigation breadcrumb (Home link); all CSS written inline in `<style>` tag
- [X] T034 [P] [US1] Create `tgexport/render/templates/index.html.j2`: extends `base.html.j2`; table/list of all chats with title, type badge, message count, last-message date (from DB); each entry links to `<chat_id>/page_001.html`
- [X] T035 [US1] Create `tgexport/render/templates/chat.html.j2`: extends `base.html.j2`; messages in chronological order; per-message block: sender name, date, text (with HTML escaping), inline `<img>` for photos (relative `src`), `<video>` for videos, `<audio>` for voice/audio, `<a href>` for documents/stickers; reply-quote block (quoted sender + truncated text + anchor link); forwarded-from block; reactions row (emoji + count); edit marker ("edited"); deletion placeholder; pagination nav links (prev / next / page N of M)
- [X] T036 [US1] Create `tgexport/render/renderer.py`: `render_all(conn, output_dir, page_size=500, chat_ids=None) -> None` — queries all chats, for each calls `render_chat`; writes `index.html`; `render_chat(conn, chat_id, output_dir, page_size=500) -> None` — paginates messages from DB using `get_messages(offset, limit)`, renders each page file `page_NNN.html`, creates `<chat_id>/` directory

### CLI Export & Render Commands

- [X] T037 [US1] Create `tgexport/cli/cmd_export.py`: `@cli.command("export")` with options `--include`, `--exclude`, `--type`, `--no-media`, `--no-render`, `--batch-size` (default 200); opens DB; opens Telegram session; iterates all (or filtered) dialogs; for each dialog fetches full history via `iter_messages` (no watermark — full scan); upserts every message and attachment ref; downloads attachments (unless `--no-media`); updates `sync_state` watermark per batch; after all chats triggers `render_all` (unless `--no-render`); rich progress bar per dialog; summary table at end
- [X] T038 [P] [US1] Create `tgexport/cli/cmd_render.py`: `@cli.command("render")` with `--include`, `--page-size`, `--force`; opens DB (read-only path); calls `render_all`; prints output directory path on completion

**Checkpoint**: `tgexport export` on a real account completes without error. `output/index.html` opens in browser with no internet. All chat pages load. Images render inline. `pytest tests/unit/` passes.

---

## Phase 4: User Story 2 — Incremental Synchronisation (Priority: P2)

**Goal**: Re-run fetches only new messages; zero duplicates; resumes interrupted runs.

**Independent Test**: Run export; record message count; run again with no new messages; verify count unchanged. Run export; interrupt at 30 s; resume; verify no duplicates (SQL: `GROUP BY id, chat_id HAVING COUNT(*)>1` = empty).

### Tests

- [X] T039 [P] [US2] Write `tests/unit/storage/test_incremental.py`: test that upserting the same `RawMessage` twice yields exactly one DB row for message, one for each edit, one per reaction emoji; test `get_pending_attachments` returns only rows where `sha256 IS NULL`

### Implementation

- [X] T040 [US2] Update `tgexport/fetch/messages.py`: ensure `min_id` and `max_id` parameters are correctly forwarded to `client.iter_messages`; document in docstring that `max_id` is used for backward-scan resumption and `min_id` for forward incremental sync
- [X] T041 [US2] Update `tgexport/cli/cmd_export.py` incremental path: before fetching each dialog, read `sync_state`; if `history_complete=0` resume backward scan with `max_id=oldest_message_id`; if `history_complete=1` do forward sync with `min_id=last_message_id`; commit watermark every `--batch-size` messages; after full history mark `history_complete=1`
- [X] T042 [P] [US2] Update `tgexport/cli/cmd_export.py` attachment retry: after message fetch loop call `get_pending_attachments(conn, chat_id)` and attempt download for each pending row; resolve or skip on error

**Checkpoint**: Second `tgexport export` run with no new Telegram activity produces zero new rows and zero new files. Resume after Ctrl-C yields no duplicates.

---

## Phase 5: User Story 3 — Selective Export (Priority: P3)

**Goal**: Filter dialogs by whitelist, blacklist, and/or type before export.

**Independent Test**: Run `tgexport export --include <ID1> <ID2>`; verify only those two chats appear in DB and HTML.

### Tests

- [X] T043 [P] [US3] Write `tests/unit/test_filter.py`: test `ChatFilter` matches by integer ID; test matches by exact title string; test blacklist exclusion overrides whitelist; test type filter `private` excludes groups; test no-filter returns all

### Implementation

- [X] T044 [P] [US3] Add `ChatFilter` dataclass to `tgexport/core/config.py`: fields `include: list[str | int]`, `exclude: list[str | int]`, `types: list[Literal["private","group","supergroup","channel"]] | None`; method `matches(dialog: RawDialog) -> bool`
- [X] T045 [P] [US3] Update `tgexport/fetch/dialogs.py`: accept optional `chat_filter: ChatFilter | None`; apply `filter.matches(dialog)` and skip non-matching dialogs (log skipped at DEBUG)
- [X] T046 [P] [US3] Update `tgexport/cli/cmd_export.py`: parse `--include`, `--exclude`, `--type` options into `ChatFilter`; pass to `iter_dialogs`

**Checkpoint**: `tgexport export --include <ID>` processes exactly one chat. `tgexport export --type channel` skips private chats and groups in logs.

---

## Phase 6: User Story 4 — Offline Archive Quality (Priority: P4)

**Goal**: Archive is fully self-contained offline with rich rendering: inline media,
reply threading, forwarded attribution, reactions, edit history, deletion markers.

**Independent Test**: Copy `output/` to a machine with no network. Open `index.html`.
Navigate to a chat with photos, replies, and reactions. All features present with no
404 or external requests (verify via browser DevTools Network tab).

### Tests

- [X] T047 [P] [US4] Write `tests/unit/render/test_offline.py`: render a fixture chat; parse HTML with `html.parser`; assert no `<link>`, `<script src>`, or `<img src>` pointing to `http://` or `https://`; assert all `src` attributes start with relative path

### Implementation

- [X] T048 [US4] Update `tgexport/render/templates/base.html.j2`: audit and remove any remaining external resource references; inline all CSS; add `<meta charset>` and `<meta name="viewport">`; ensure no JavaScript is required for basic reading
- [X] T049 [US4] Update `tgexport/render/templates/chat.html.j2` reply block: resolve reply-to message from DB in renderer; display quoted sender name, quoted text excerpt (truncated at 120 chars), anchor link `href="#msg-<id>"`; if reply-to not in DB display "(message unavailable)" placeholder
- [X] T050 [P] [US4] Update `tgexport/render/templates/chat.html.j2` reactions block: display each emoji + count; sort by count descending; wrap at 8 reactions per row
- [X] T051 [US4] Update `tgexport/render/templates/chat.html.j2` edit/deletion: for edited messages show "✎ edited" marker with `<details><summary>` expanding to edit history (each version with timestamp); for deleted messages show "[message deleted]" placeholder in muted style
- [X] T052 [P] [US4] Update `tgexport/render/templates/index.html.j2`: sort chats by most-recent message date (DESC); show per-chat: title, type badge, message count, last-message date; display "(empty)" if message count = 0
- [X] T053 [US4] Update `tgexport/render/renderer.py`: pass reply-to message objects to template context (batch-load from DB per page to avoid N+1 queries); pass edit history per message; ensure `render_chat` creates `<chat_id>/` subdirectory if absent

**Checkpoint**: `pytest tests/unit/render/test_offline.py` passes. Manual offline test: copy output to `/tmp/`, open in browser with network disabled, all pages render fully.

---

## Phase 7: User Story 5 — Live-Mode Skeleton (Priority: P5, Future)

**Goal**: Register the `watch` command and `run_daemon` interface with frozen signatures
so live mode can be implemented later without CLI or layer changes.
**These tasks do NOT implement live functionality.**

- [X] T054 [P] Create `tgexport/live/__init__.py`: empty module init
- [X] T055 [P] Create `tgexport/live/daemon.py`: `async def run_daemon(cfg: Config, conn: aiosqlite.Connection, output: Path) -> None` — body raises `NotImplementedError("Live mode is not implemented in this release.")`; add `# TODO(live): register NewMessage, MessageEdited, MessageDeleted handlers` comment outlining the future implementation
- [X] T056 [P] Create `tgexport/cli/cmd_watch.py`: `@cli.command("watch")` — calls `asyncio.run(run_daemon(cfg, conn, output_dir))`; catches `NotImplementedError`, prints message to stderr, exits with code 3
- [X] T057 [P] Register `cmd_watch` in `tgexport/cli/main.py` (add `from tgexport.cli.cmd_watch import watch; cli.add_command(watch)`)

**Checkpoint**: `tgexport watch` exits with code 3 and prints a human-readable "not implemented" message. `echo $?` returns 3.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates, security hardening, and documentation review.

- [X] T058 [P] Security: update `tgexport/fetch/client.py` to `chmod 600` the session file immediately after creation; update `tgexport/storage/db.py` to `chmod 600` the DB file and `chmod 700` the `data/` directory on first open
- [X] T059 Run `mypy --strict tgexport/` and fix all type errors; ensure no `type: ignore` comments without explanatory note
- [X] T060 [P] Run `ruff check tgexport/ tests/` and fix all lint violations; run `ruff format tgexport/ tests/`
- [X] T061 [P] Run full test suite `pytest tests/ -v` and confirm all pass; add any missing edge-case tests surfaced during mypy/ruff pass
- [ ] T062 [P] Validate `quickstart.md` scenarios against the implemented CLI: run each scenario from `specs/001-telegram-profile-export/quickstart.md` in a staging environment and confirm expected outcomes
- [X] T063 [P] Create `tgexport/core/__init__.py`, `tgexport/fetch/__init__.py`, `tgexport/storage/__init__.py`, `tgexport/render/__init__.py`, `tgexport/cli/__init__.py` with `__all__` lists bounding public API of each layer

**Checkpoint**: `mypy --strict tgexport/` exits 0. `ruff check tgexport/ tests/` exits 0. `pytest tests/ -v` exits 0. All quickstart scenarios pass manually.

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)
  └─► Phase 2 (Foundation) ──────────────────► Phase 3 (US1)
                                                   │
                               ┌───────────────────┘
                               ▼
                           Phase 4 (US2) ──► Phase 5 (US3)
                                                   │
                                                   ▼
                                            Phase 6 (US4)
                                                   │
                                    ┌──────────────┘
                                    ▼               ▼
                             Phase 7 (US5)   Phase 8 (Polish)
                             (live skeleton)
```

Phases 7 and 8 are independent of each other after Phase 6.

### Within Phase 2 (Foundation)

T006 → T007 → T008 (config must exist before DB can reference it)
T007 parallel with T009, T010, T011
T012 requires T006
T013 requires T006
T014 requires T012, T013
T015, T016 require T008

### Within Phase 3 (US1)

Tests (T017–T021, T030–T031) → Storage impl (T022–T026) → Fetch (T027–T029) → Render (T032–T036) → CLI (T037–T038)
Within storage impl: T022, T023 can parallel; T024, T025, T026 can parallel
Within fetch: T027 parallel with T028 partial; T029 requires T024 (for resolve_attachment call)
Within render: T033, T034 parallel; T032, T035 after T034; T036 after T035

### User Story Dependencies

- US1 (P1): Depends on Phase 2 only
- US2 (P2): Depends on US1 completion (extends cmd_export.py and messages.py)
- US3 (P3): Depends on Phase 2 only (parallel with US1/US2 if different developers)
- US4 (P4): Depends on US1 (extends render layer)
- US5 live skeleton: Independent of US2–US4; can be done any time after Phase 3

### Parallel Opportunities

All tasks marked [P] have no dependencies on in-progress tasks in their phase.

```bash
# Phase 2 can launch in parallel after Phase 1:
T006 (config)  |  T007+T008 (schema)  |  T009 (models)  |  T010 (upsert)  |  T011 (rate_limiter)
               └──────────────────────────────────────────────────────────────┘
                                after: T012, T013, T014, T015, T016

# Phase 3 storage tests launch together:
T017  |  T018  |  T019  |  T020  |  T021

# Phase 3 storage impl launches after tests written:
T022  |  T023  |  T024  |  T025  |  T026
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundation ← BLOCKS all stories
3. Complete Phase 3: US1 ← first working export
4. **STOP and VALIDATE**: Run quickstart Scenario 1; verify DB + HTML offline
5. If validated: continue to US2

### Incremental Delivery

1. Phase 1 + Phase 2 → Foundation ready
2. Phase 3 (US1) → Working export MVP
3. Phase 4 (US2) → Resumable incremental sync
4. Phase 5 (US3) → Selective export
5. Phase 6 (US4) → Full offline render quality
6. Phase 7 (US5) → Live-mode stubs (non-blocking; can be done at any time)
7. Phase 8 (Polish) → Quality gate before any release

### Notes

- [P] tasks in the same phase use different files; safe to parallelize
- Storage and render layers MUST have failing tests before implementation begins (TDD)
- Commit watermark every `--batch-size` messages (default 200) — not per message
- Never import from `fetch` inside `render`; mypy + `__all__` bounds enforce this
- Verify `pytest tests/unit/` passes at each phase checkpoint before advancing
