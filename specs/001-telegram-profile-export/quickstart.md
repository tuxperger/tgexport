# Quickstart Validation Guide: Telegram Profile Export

**Date**: 2026-07-05

This guide describes how to validate that the implemented tool works end-to-end.
It covers prerequisites, environment setup, and runnable validation scenarios.

---

## Prerequisites

1. A Telegram account with at least one personal chat, one group, and one channel.
2. `api_id` and `api_hash` from [https://my.telegram.org/apps](https://my.telegram.org/apps).
3. Nix with flakes enabled (`nix develop` must work in the project root).

---

## Environment Setup

```bash
# Enter the Nix development shell
nix develop

# Copy the example env file and fill in your credentials
cp .env.example .env
chmod 600 .env
# Edit .env: set TG_API_ID and TG_API_HASH

# Verify the CLI entry point is available
tgexport --help
```

---

## Scenario 1 — Full Initial Export (US1)

**What it validates**: authentication, dialog enumeration, complete history fetch,
attachment download, HTML generation.

```bash
# Step 1: Authenticate (creates session file)
tgexport login

# Step 2: Export everything
tgexport export

# Step 3: Open the archive
xdg-open output/index.html   # or open output/index.html on macOS
```

**Expected outcomes**:
- `data/session.session` exists and has permissions 600.
- `data/archive.db` exists and is non-empty.
- `output/index.html` lists all exported chats.
- For each chat, `output/<chat_id>/page_001.html` (and further pages if needed) exists.
- At least one attachment file appears under `data/media/`.

---

## Scenario 2 — Incremental Sync (US2)

**What it validates**: watermark correctness, zero duplication on re-run.

```bash
# Record current message count in DB
sqlite3 data/archive.db "SELECT COUNT(*) FROM messages;" > /tmp/before.txt

# Run export again (should fetch only new messages)
tgexport export

# Compare counts: delta should equal only messages sent since last run
sqlite3 data/archive.db "SELECT COUNT(*) FROM messages;" > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt
```

**Expected outcome**: The count increases only by the number of messages actually sent
since the previous run. Running export with no new Telegram activity produces zero
new rows.

---

## Scenario 3 — Interrupted and Resumed Export (US2, SC-004)

**What it validates**: idempotency and resumability.

```bash
# Start a fresh export and interrupt it mid-way
tgexport export &
sleep 10
kill %1

# Resume — should continue from last watermark, not restart
tgexport export

# Verify no duplicates
sqlite3 data/archive.db "
  SELECT chat_id, id, COUNT(*) c
  FROM messages
  GROUP BY chat_id, id
  HAVING c > 1;
"
```

**Expected outcome**: The duplicate-check query returns zero rows.

---

## Scenario 4 — Selective Export (US3)

**What it validates**: whitelist, blacklist, and type filters.

```bash
# Export only two specific chats (replace IDs with real ones from your account)
tgexport export --include 123456789 987654321

# Verify only those chats appear in the database
sqlite3 data/archive.db "SELECT id, title FROM chats;"

# Export only personal (private) chats
tgexport export --type private
```

**Expected outcome**: Only the specified chats appear in `data/archive.db` and
`output/index.html`.

---

## Scenario 5 — Offline Archive (US4)

**What it validates**: full self-containment of HTML output.

```bash
# Copy the output directory to a location with no internet
cp -r output /tmp/tg-archive-test

# Simulate offline (optional: disable network interface or use a VM with no network)
# Open the copied archive
xdg-open /tmp/tg-archive-test/index.html
```

**Checks**:
- All pages load without browser network requests (verify via browser DevTools Network tab).
- Images render inline (visible without clicking).
- Audio player is present for voice messages and uses a local `src`.
- Document links use relative `../../data/media/...` paths (or equivalent local path).
- Reply quotes display the quoted message text inline.
- Forwarded-from attribution is visible.
- Edited messages show an "edited" marker; deleted messages show a placeholder.

---

## Scenario 6 — Render-Only Pass (offline)

**What it validates**: render layer works independently of fetch layer.

```bash
# With network disabled (disconnect or set TG_API_ID to a dummy value):
tgexport render

# Should complete without any Telegram API calls
```

**Expected outcome**: `output/` is regenerated from the existing `data/archive.db`;
no network error is thrown.

---

## Scenario 7 — Watch Command Stub

**What it validates**: `watch` command is registered and returns the correct exit code.

```bash
tgexport watch
echo "Exit code: $?"
```

**Expected outcome**: Exit code is 3; a human-readable "not implemented" message is
printed to stderr.

---

## Unit Test Gate

```bash
# Inside nix develop:
mypy --strict tgexport/
ruff check tgexport/ tests/
ruff format --check tgexport/ tests/
pytest tests/ -v
```

**Expected outcome**: All commands exit 0.

---

## References

- CLI command reference: [contracts/cli.md](contracts/cli.md)
- Data model & schema: [data-model.md](data-model.md)
- Layer interfaces: [contracts/layer-interfaces.md](contracts/layer-interfaces.md)
- Research decisions: [research.md](research.md)
