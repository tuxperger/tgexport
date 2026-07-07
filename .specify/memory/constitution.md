<!--
SYNC IMPACT REPORT
==================
Version change: (none) → 1.0.0 (initial ratification)

Modified principles: N/A — first fill of template

Added sections:
  - Core Principles (I–VII): all seven principles defined
  - Prohibited Actions: explicit read-only guarantees
  - Rate Limiting Policy: API back-off and throttling rules
  - Governance

Removed sections: N/A

Templates reviewed:
  - .specify/templates/plan-template.md  ✅ no updates needed
    (Constitution Check section is generic; gates will be derived per-plan)
  - .specify/templates/spec-template.md  ✅ no updates needed
  - .specify/templates/tasks-template.md ✅ no updates needed

Deferred items: none
-->

# Telegram Exporter Constitution

## Core Principles

### I. Account Safety and Read-Only

The tool operates via a user MTProto account but MUST perform only read and download
operations. It MUST NEVER send messages, join or leave chats, delete or edit any data
in Telegram, or take any action that modifies remote state.

All API calls MUST respect Telegram's rate limits:
- FloodWait errors MUST be caught and the mandated wait time honoured before retrying.
- Retries MUST use exponential back-off (see Rate Limiting Policy section).
- Concurrent request counts MUST be bounded; a global request throttle is REQUIRED.

Rationale: any write or rate-limit violation can trigger account restrictions. The
tool archives only the owner's own data and MUST NOT be repurposed for any other account.

### II. Local-First and Privacy

All artefacts — Telethon session file, SQLite database, downloaded attachments, and
generated HTML — MUST be stored exclusively on the local machine.

No data, metadata, or diagnostic information MUST be sent to any third party or
published to any remote service.

Secrets (`api_id`, `api_hash`, session file path) MUST be read from environment
variables or a local config file, MUST be stored with filesystem permissions `600`,
and MUST NEVER appear in the repository (`.gitignore` entries REQUIRED).

### III. Idempotency and Resumability

Any export run MUST be safe to interrupt and restart without duplicating stored data.

Each chat MUST maintain a persistent watermark (cursor) recording the last-fetched
message ID. Subsequent runs MUST fetch only messages newer than the watermark.

Attachment downloads MUST be deduplicated: if a file with the same content hash
already exists on disk, a re-download MUST be skipped.

### IV. Layer Separation

The codebase MUST be organised into four independent layers with no cross-layer
imports in the upward direction:

| Layer     | Responsibility                                   |
|-----------|--------------------------------------------------|
| `fetch`   | All Telegram API access (Telethon)               |
| `storage` | SQLite read/write; canonical source of truth     |
| `render`  | HTML generation from DB; zero network access     |
| `live`    | Real-time update daemon; delegates to fetch + storage |

The `render` layer and any report generation MUST operate entirely offline from the
local database. Importing from `fetch` inside `render` is FORBIDDEN.

### V. Reproducible Environment

The ONLY supported way to build and run the project is via the Nix flake (`nix develop`).
All runtime and development dependencies MUST be declared in `flake.nix` and locked in
`flake.lock`. Installing packages globally outside the flake (pip, system packages) is
FORBIDDEN and MUST NOT be documented as an alternative workflow.

### VI. Code Quality

- Python 3.12+ REQUIRED.
- All public and internal interfaces MUST carry full type hints.
- `mypy --strict` MUST pass with zero errors.
- `ruff` is the sole linter and formatter; its configuration is authoritative.
- The `storage` and `render` layers MUST have pytest unit test coverage. Tests MUST NOT
  make network calls; any Telegram interaction MUST be mocked.
- CI (if added) MUST run mypy, ruff, and pytest before merge.

### VII. Data Completeness and Preservation

The `storage` layer MUST persist, for every message:

- Author (sender ID + name snapshot)
- Timestamp (UTC)
- Reply-to chain (`reply_to_msg_id`)
- Forwarded-from metadata
- Reactions (emoji + count)
- Edit history (all known versions with edit timestamps)
- Deletion flag
- Attachment type and local file path

Raw data received from Telegram MUST be stored faithfully so that HTML output can be
regenerated from the database alone, without a second round-trip to Telegram.

## Prohibited Actions

The following actions are categorically forbidden at any point in the codebase and
MUST be enforced through code review and, where possible, static checks:

- Sending any message or reaction to Telegram.
- Joining, leaving, creating, or archiving any chat or channel.
- Deleting or editing any message, media, or contact.
- Uploading or publishing any data to any external service.
- Storing secrets in any file that is tracked by git.
- Installing runtime dependencies outside the Nix flake.

## Rate Limiting Policy

All calls to the Telegram MTProto API MUST follow this back-off policy:

1. **FloodWait**: on `FloodWaitError`, sleep exactly `error.seconds + 1` seconds before
   retrying. Log the event at WARNING level.
2. **Exponential back-off**: for other transient errors, retry with delays of
   `min(2^attempt, 60)` seconds, up to 5 attempts.
3. **Concurrency cap**: no more than 3 concurrent download coroutines at any time.
4. **Global throttle**: sustained request rate MUST NOT exceed 20 requests per second
   across all operations.

These limits are conservative by design to protect the user's account.

## Governance

This Constitution supersedes all other project practices. Amendments MUST:

1. Update this file with a new version number following semantic versioning:
   - MAJOR: removal or redefinition of a principle that breaks prior behaviour.
   - MINOR: addition of a new principle or material expansion of an existing one.
   - PATCH: clarifications, wording corrections, non-semantic refinements.
2. Record the amendment in the Sync Impact Report HTML comment at the top of this file.
3. Update `LAST_AMENDED_DATE` and propagate changes to affected templates.

Compliance review: every plan (`/speckit-plan`) MUST include a Constitution Check gate
that explicitly verifies adherence to all seven Core Principles before implementation
begins. Any deviation requires a documented justification in the plan's Complexity
Tracking table.

**Version**: 1.0.0 | **Ratified**: 2026-07-05 | **Last Amended**: 2026-07-05
