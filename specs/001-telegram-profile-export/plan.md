# Implementation Plan: Telegram Profile Export

**Branch**: `001-telegram-profile-export` | **Date**: 2026-07-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-telegram-profile-export/spec.md`

## Summary

Build a personal CLI tool that archives a user's entire Telegram account into a
self-contained offline HTML archive. The tool authenticates via MTProto (Telethon),
enumerates all accessible dialogs, fetches complete message history with all metadata
(replies, forwards, reactions, edits, deletions), downloads all attachments, stores
everything in a local SQLite database as the canonical source of truth, and generates
static HTML from that database via Jinja2. Subsequent runs are incremental (watermark
per chat). The architecture has four strict layers — fetch / storage / render / live —
with live as a future-ready skeleton only.

## Technical Context

**Language/Version**: Python 3.12+ with asyncio throughout

**Primary Dependencies**:
- Telethon + cryptg (MTProto client; cryptg provides native crypto acceleration)
- aiosqlite (async SQLite access)
- Jinja2 (HTML templating)
- rich (progress bars, console output)
- python-dotenv (`.env` config loading)
- pytest + pytest-asyncio (testing)
- mypy (strict mode)
- ruff (linting + formatting)

**Storage**: SQLite via aiosqlite; attachment binaries on local filesystem

**Testing**: pytest with pytest-asyncio; mypy --strict; ruff check + format

**Target Platform**: Linux (Nix flake environment); single-user local tool

**Project Type**: CLI tool (asyncio, subcommand-based)

**Performance Goals**:
- Handle accounts with 100+ dialogs and 500k+ total messages without OOM
- Respect Telegram rate limits: ≤ 20 API req/s global, ≤ 3 concurrent downloads
- Resume interrupted runs with zero re-work (watermark-based idempotency)

**Constraints**:
- HTML archive fully offline (all assets via relative local paths)
- No data leaves the local machine; no third-party services
- `render` layer must work with zero network access
- Secrets must never appear in the repository

**Scale/Scope**: Personal single-user tool; single account per invocation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Gate | Status |
|-----------|------|--------|
| I. Account Safety / Read-Only | `fetch` layer exposes NO write methods; all Telegram calls are read-only; FloodWait + backoff required | ✅ PASS |
| II. Local-First & Privacy | No network in render; secrets from env/file with 600 perms; `.gitignore` must cover session, DB, `.env` | ✅ PASS |
| III. Idempotency & Resumability | Per-chat `sync_state` watermark; all writes are upserts (INSERT OR REPLACE / ON CONFLICT); attachment dedup by SHA-256 | ✅ PASS |
| IV. Layer Separation | Four layers: `fetch` / `storage` / `render` / `live`; `render` MUST NOT import `fetch`; `cli` orchestrates; live is skeleton | ✅ PASS |
| V. Reproducible Environment | Nix flake is sole entry point; all deps declared in `flake.nix` + `flake.lock`; no pip install outside flake | ✅ PASS |
| VI. Code Quality | Python 3.12+; full type hints; mypy --strict; ruff; pytest for storage + render; no network in tests | ✅ PASS |
| VII. Data Completeness | All message fields stored: sender, date, reply chain, forwards, reactions, edit history, deletion flag, attachment metadata | ✅ PASS |

No violations. No Complexity Tracking entries required.

## Project Structure

### Documentation (this feature)

```text
specs/001-telegram-profile-export/
├── plan.md              ← this file
├── research.md          ← Phase 0
├── data-model.md        ← Phase 1
├── quickstart.md        ← Phase 1
├── contracts/
│   ├── cli.md           ← CLI command contract
│   └── layer-interfaces.md  ← Python layer APIs
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
tgexport/
├── __init__.py
├── core/
│   └── config.py            # Settings dataclass; env + .env loading
├── fetch/
│   ├── __init__.py
│   ├── client.py            # Telethon TelegramClient wrapper; session mgmt
│   ├── dialogs.py           # iter_dialogs → RawDialog
│   ├── messages.py          # iter_messages with pagination → RawMessage
│   ├── media.py             # download_media → bytes / disk write
│   └── rate_limiter.py      # FloodWait handler, backoff decorator, semaphores
├── storage/
│   ├── __init__.py
│   ├── db.py                # Connection pool, migration runner
│   ├── schema.sql           # Authoritative DDL (versioned migrations)
│   ├── models.py            # Typed dataclasses: Chat, Message, Attachment, …
│   ├── repos/
│   │   ├── chats.py
│   │   ├── messages.py
│   │   ├── attachments.py
│   │   ├── users.py
│   │   ├── reactions.py
│   │   └── sync_state.py
│   └── upsert.py            # Shared idempotent write helpers
├── render/
│   ├── __init__.py
│   ├── renderer.py          # Orchestrates full / partial re-render
│   ├── filters.py           # Jinja2 custom filters (timestamp, media type…)
│   └── templates/
│       ├── base.html.j2     # Common layout (CSS inline or link to static/)
│       ├── index.html.j2    # Chat list
│       └── chat.html.j2     # Per-chat page (paginated)
├── live/
│   ├── __init__.py
│   └── daemon.py            # SKELETON: event loop stub; NewMessage/Edit/Delete hooks
└── cli/
    ├── __init__.py
    ├── main.py              # Entry point; subcommand dispatch
    ├── cmd_login.py         # `tgexport login`
    ├── cmd_export.py        # `tgexport export`
    ├── cmd_render.py        # `tgexport render`
    └── cmd_watch.py         # `tgexport watch` (stub → NotImplementedError)

tests/
├── conftest.py
├── unit/
│   ├── storage/
│   │   ├── test_upsert.py
│   │   ├── test_repos.py
│   │   └── test_migrations.py
│   └── render/
│       ├── test_renderer.py
│       └── test_filters.py
└── integration/             # Optional; kept for future real-DB integration tests

flake.nix
flake.lock
pyproject.toml               # tool.mypy, tool.ruff, tool.pytest sections
.env.example                 # Template with all required vars, no real values
.gitignore                   # session file, *.db, .env, media/, output/
```

**Structure Decision**: Single project layout. No frontend/backend split needed
(pure CLI + offline static files). Package name `tgexport`; installed as `tgexport`
entry point via pyproject.toml.

## Complexity Tracking

*No violations to justify.*
