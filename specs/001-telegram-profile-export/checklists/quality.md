# Quality Checklist: Telegram Profile Export

**Purpose**: Validate that requirements across six risk dimensions are measurable,
unambiguous, and complete — before implementation begins.
**Created**: 2026-07-05
**Feature**: [spec.md](../spec.md) · [plan.md](../plan.md) · [data-model.md](../data-model.md) · [contracts/](../contracts/)

---

## Account Safety & Telegram Rate Limit Compliance

- [ ] CHK001 — Is the prohibition on write operations stated as a specific enumerable list of forbidden Telegram API call categories (send, join, leave, delete, edit), rather than a general "read-only" statement that could be ambiguous at implementation time? [Clarity, Constitution §I, Spec §FR-001]
- [ ] CHK002 — Is the FloodWait handling formula fully specified (sleep = `error.seconds + 1`, log level = WARNING) rather than described as "honour flood wait"? [Measurability, Constitution §Rate Limiting Policy, research.md §5]
- [ ] CHK003 — Is the exponential back-off policy quantified with exact values (max attempts = 5, delay = `min(2^attempt, 60)` seconds) for all non-FloodWait transient errors? [Measurability, Constitution §Rate Limiting Policy]
- [ ] CHK004 — Is the concurrent download cap expressed as an exact integer (3), not as "limited" or "bounded"? [Measurability, Constitution §Rate Limiting Policy, contracts/layer-interfaces.md]
- [ ] CHK005 — Is the global request throttle specified as a numeric threshold (20 req/s), and is the throttle scope defined (all API calls, or only message-fetch calls)? [Clarity, Constitution §Rate Limiting Policy]
- [ ] CHK006 — Does the spec define the behavior when the maximum retry count is exhausted — specifically, does the tool skip the current chat and continue, or abort the entire run? [Coverage, Edge Case, Spec §Edge Cases]
- [ ] CHK007 — Are requirements for FloodWait and for other transient errors (network timeout, server 500) separated into distinct retry strategies, or conflated under a single policy? [Clarity, Spec §FR-013, research.md §5]
- [ ] CHK008 — Is the read-only constraint enforced at a layer boundary (i.e., does the `fetch` module API expose no mutation methods), or is it stated only as a behavioral expectation with no architectural enforcement? [Measurability, Constitution §I, contracts/layer-interfaces.md]

---

## Privacy & Secrets Handling

- [ ] CHK009 — Are filesystem permission requirements stated for every secret-bearing artefact (session file: 600, DB file: 600, data directory: 700), or only stated once as a general "restricted permissions"? [Measurability, Constitution §II, Spec §Assumptions]
- [ ] CHK010 — Does the spec enumerate all files and directories that MUST be listed in `.gitignore` (`.env`, `*.session`, `*.db`, `data/`, `output/`, `media/`), or is this left as a developer convention? [Completeness, Constitution §II]
- [ ] CHK011 — Is there a requirement that `TG_API_ID` and `TG_API_HASH` are validated for presence at startup, with a defined error message and exit code, rather than failing later with an obscure Telegram API error? [Completeness, contracts/cli.md]
- [ ] CHK012 — Does the spec prohibit passing credentials via command-line arguments (to avoid shell history leakage), or does it allow any delivery mechanism? [Gap, Privacy]
- [ ] CHK013 — Is "strictly local" defined to include a prohibition on telemetry, crash reporting, or diagnostic data leaving the machine, or does it cover only explicit upload/publish actions? [Clarity, Spec §Clarifications]
- [ ] CHK014 — Is the expected `.env` file permission (600) stated as a requirement, and is the behavior defined if the file has overly permissive permissions (warn, refuse to start, or proceed)? [Edge Case, Gap]

---

## Chat Type & Attachment Coverage

- [ ] CHK015 — Are requirements defined for all four dialog types (private, group, supergroup, channel) individually, or stated only as "all dialogs"? Are private/invite-only groups and channels explicitly called out? [Completeness, Spec §FR-003, Spec §US1]
- [ ] CHK016 — Is the enumeration of attachment categories exhaustive and explicit (photo, video, audio, voice, video_note, document, sticker, gif — exactly eight types), or left as an open-ended list? [Completeness, Spec §FR-006, data-model.md §attachments]
- [ ] CHK017 — Are sticker and GIF rendering requirements specified in the HTML output (rendered as inline images, or as file download links), or is their visual treatment undefined? [Clarity, Gap]
- [ ] CHK018 — Is behavior defined for Telegram "album" messages (multiple photos/videos in a single grouped message)? Are albums represented as one message with multiple attachments, or as separate message rows? [Coverage, Edge Case, Gap]
- [ ] CHK019 — Are service message types enumerated in requirements (e.g., pinned message, user joined/left, call started)? Is each type required to be stored and rendered, or only some? [Completeness, Spec §FR-005]
- [ ] CHK020 — Is the deduplication rule for attachments with identical SHA-256 but different Telegram file IDs explicitly stated (one file on disk, multiple `attachments` rows referencing it)? [Clarity, Spec §FR-007, data-model.md §Attachment/File]
- [ ] CHK021 — Are the whitelist/blacklist filter matching rules specified precisely — is matching by ID exact-integer equality, and matching by name case-sensitive/insensitive substring or exact? [Clarity, Spec §FR-004, contracts/cli.md]

---

## Idempotency & Resumability

- [ ] CHK022 — Is the watermark commit granularity specified as a configurable default (e.g., every 200 messages), or is it left as an undefined implementation detail? [Measurability, research.md §6, contracts/cli.md `--batch-size`]
- [ ] CHK023 — Are upsert semantics specified for each table that accepts repeated writes (INSERT OR REPLACE, or INSERT … ON CONFLICT DO UPDATE with named columns), rather than stated as "idempotent writes"? [Clarity, data-model.md]
- [ ] CHK024 — Is the resumption path for the initial backward scan (resume from `oldest_message_id` going further back) distinguished from the incremental forward sync path (fetch from `last_message_id` upward), with each condition clearly stated? [Clarity, data-model.md §SyncState, research.md §6]
- [ ] CHK025 — Is "idempotent output" in SC-004 defined in measurable terms (e.g., identical row count, identical SHA-256 for all attachment files) rather than stated as a qualitative expectation? [Measurability, Spec §SC-004]
- [ ] CHK026 — Is idempotency coverage specified for derived tables (reactions, edits) as well as the base messages table, or only for messages? [Completeness, Gap]
- [ ] CHK027 — Is the behavior defined for an attachment whose download was interrupted (sha256 = NULL in the DB) on a subsequent run — is it automatically retried, or must the user take manual action? [Edge Case, data-model.md §Attachment/File]
- [ ] CHK028 — Is "zero duplicate messages" in SC-002 measurable with a concrete SQL assertion (GROUP BY id, chat_id HAVING COUNT(*) > 1 = 0), or stated only as a qualitative goal? [Measurability, Spec §SC-002]

---

## Render Correctness

- [ ] CHK029 — Are reply-quote display requirements specified with exact content (quoted sender name, quoted text excerpt, visual indicator, and link/anchor to the original message), or stated as "show reply context"? [Clarity, Spec §FR-011]
- [ ] CHK030 — Is the behavior defined when a reply-to message is not present in the local database (the original message was deleted before export or outside the selected export window)? [Edge Case, Spec §Edge Cases]
- [ ] CHK031 — Are forwarded-message attribution requirements complete — does the spec require original sender name, original chat/channel name, and original timestamp, or only "forwarded-from attribution"? [Completeness, Spec §FR-011]
- [ ] CHK032 — Are reaction display requirements quantified: is there a specified maximum number of distinct emoji displayed, a minimum count threshold for display, and defined layout (grouped vs. individual)? [Clarity, Gap]
- [ ] CHK033 — Is the edit-history display format specified (collapsed/expandable, timeline of versions with timestamps, or current-only with an "edited" marker), or left as a rendering choice? [Clarity, Spec §FR-011]
- [ ] CHK034 — Are video and audio HTML rendering requirements defined with a fallback behavior for browsers that do not support the codec (download link, error message, or silent omission)? [Coverage, Gap]
- [ ] CHK035 — Is the HTML pagination page size defined as a specific numeric value (or a named configurable default), and is the navigation UI specified (prev/next links, page numbers)? [Measurability, research.md §8, contracts/cli.md `--page-size`]
- [ ] CHK036 — Are index page requirements specified with a defined sort order (by most-recent message date, alphabetical, or user-configurable) and the data fields required per chat entry (title, message count, last message date)? [Completeness, Spec §FR-010]
- [ ] CHK037 — Is the self-containment requirement in FR-012 and SC-003 explicit that no external resources are loaded (no CDN CSS/fonts/JS, no remote images), measurable via browser Network tab with network disabled? [Measurability, Spec §FR-012, Spec §SC-003]

---

## Live-Mode Architecture Readiness

- [ ] CHK038 — Does the spec identify which database schema elements are required in the first release specifically to avoid destructive schema changes when live mode is added (e.g., the `sync_state` table structure, the `files` deduplication table)? [Completeness, Spec §FR-016, data-model.md §Migration Strategy]
- [ ] CHK039 — Is the `live/daemon.py` function signature frozen as a contract (fixed parameters: `cfg`, `conn`, `output`) so that `cli/cmd_watch.py` can call it without change after live mode is implemented? [Measurability, contracts/layer-interfaces.md §live layer]
- [ ] CHK040 — Are the event types the live daemon must handle (NewMessage, MessageEdited, MessageDeleted) explicitly enumerated in requirements, or implied by "real-time events"? [Completeness, Spec §US5]
- [ ] CHK041 — Is the `watch` command's exit code for the not-implemented stub (exit 3) specified in the CLI contract, enabling automated scripts to distinguish "not supported" from "error" (exit 1)? [Clarity, contracts/cli.md]
- [ ] CHK042 — Does the `render_chat()` interface contract specify that it supports single-chat incremental re-render (not only full-archive render), so the live daemon can call it per-chat without triggering a full re-render? [Completeness, contracts/layer-interfaces.md §render layer]
- [ ] CHK043 — Are the import-prohibition rules (render ✗ fetch, fetch ✗ storage, etc.) stated in a form that can be statically enforced (e.g., via mypy plugin, import linter, or explicit module `__all__` constraints), or stated as review-only conventions? [Measurability, Constitution §IV, contracts/layer-interfaces.md §Import Rules]

## Notes

- Check items off as completed: `[x]`
- Add findings inline when a gap is identified: note the spec line or section that is vague/missing
- Items marked `[Gap]` indicate requirements absent from the spec — these require additions to spec.md or plan.md before tasks are created
- Items marked `[Clarity]` indicate requirements that exist but are ambiguous — rephrase with measurable criteria
- Items marked `[Measurability]` indicate requirements that exist but cannot be objectively verified as written
