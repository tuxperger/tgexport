# Feature Specification: Telegram Profile Export

**Feature Branch**: `001-telegram-profile-export`

**Created**: 2026-07-05

**Status**: Draft

**Input**: User description: "Построй персональный инструмент, который выгружает весь профиль
Telegram пользователя в читаемый офлайн-архив."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Full Initial Export (Priority: P1)

A user who has never run the tool before wants to archive their entire Telegram account
history. They provide their credentials once, the tool authenticates, enumerates all
dialogs (personal chats, groups, supergroups, channels — including private ones), fetches
every message and downloads every attachment, stores everything in a local database, and
generates a static HTML archive. When the run finishes the user can open the index page
in a browser and read any conversation offline.

**Why this priority**: This is the foundational use case. All other stories are variations
on or extensions of this one. Without it nothing else has value.

**Independent Test**: Run the tool against a real account with at least one personal chat,
one group, and one channel. Verify that the database contains all messages and the HTML
archive opens in a browser offline with correct content.

**Acceptance Scenarios**:

1. **Given** a fresh environment with valid credentials, **When** the user runs the export
   command with no filters, **Then** all dialogs the account can access are fetched,
   all messages are stored in the local database, all attachments are downloaded, and
   a complete static HTML archive is generated with an index page listing every chat.

2. **Given** a chat containing photos, videos, voice messages, stickers, and documents,
   **When** the export runs, **Then** every attachment file is present on disk,
   deduplicated by content, and correctly linked from the HTML page for that chat.

3. **Given** the HTML archive, **When** the user opens it in a browser with no internet
   connection, **Then** all pages load, all images render inline, audio is playable from
   local files, and document links point to local paths.

---

### User Story 2 — Incremental Synchronisation (Priority: P2)

A user who has already performed a full export wants to update the archive with new
messages and attachments that appeared since the last run, without re-fetching or
duplicating already-stored data.

**Why this priority**: Essential for regular use. Without it the tool is usable only once
or requires a costly full re-download every time.

**Independent Test**: Run a full export, then send new messages in a chat, then run the
tool again. Verify that only new messages are added to the database and that the HTML is
regenerated correctly without duplicates.

**Acceptance Scenarios**:

1. **Given** an existing database with a per-chat watermark, **When** the user runs the
   export again, **Then** only messages newer than the stored watermark are fetched for
   each chat, no existing records are duplicated, and the HTML is regenerated to include
   the new messages.

2. **Given** an attachment already on disk, **When** the same file is referenced in a
   newly fetched message, **Then** it is not re-downloaded; the existing file is reused
   and linked correctly.

3. **Given** a run that is interrupted mid-way, **When** the user reruns the tool,
   **Then** it resumes from the last committed watermark without data loss or duplication.

---

### User Story 3 — Selective Export (Priority: P3)

A user wants to export only a subset of their dialogs rather than the entire account —
for example, only personal chats, only a specific group by name, or a set of chat IDs.

**Why this priority**: Useful for users with large accounts who need only specific
conversations, but the core export (US1) works without it.

**Independent Test**: Run the tool with a whitelist of two specific chat IDs. Verify that
only those two chats appear in the database and HTML, and all others are skipped entirely.

**Acceptance Scenarios**:

1. **Given** a whitelist filter (one or more chat IDs or names), **When** the export runs,
   **Then** only matching dialogs are processed; all others are skipped with a log entry.

2. **Given** a type filter (e.g., "only personal chats"), **When** the export runs,
   **Then** only dialogs of the specified type are processed.

3. **Given** a blacklist filter, **When** the export runs, **Then** matching dialogs are
   excluded and all others are processed normally.

---

### User Story 4 — Offline Archive Browsing (Priority: P4)

A user who has already generated the HTML archive wants to read their conversations
in a browser without any internet connection, with inline media, reply threading, and
readable metadata.

**Why this priority**: This is the output quality story. The archive must be fully
self-contained — it defines the value the tool delivers to the end user.

**Independent Test**: Copy the entire output directory to a machine with no internet
access. Open the index page. Navigate to several chats. Verify all media loads, reply
quotes are displayed, forwarded-message attribution is visible, and edit/deletion markers
are shown.

**Acceptance Scenarios**:

1. **Given** the generated HTML archive on a machine with no internet, **When** the user
   opens the index page, **Then** they see a list of all exported chats with names and
   message counts, each linking to a per-chat page.

2. **Given** a per-chat HTML page, **When** the user reads it, **Then** messages appear
   in chronological order with sender name, timestamp, reply-to quote (if applicable),
   forwarded-from attribution (if applicable), and reaction counts.

3. **Given** a message with an image or video, **When** the user views the chat page,
   **Then** the image renders inline as a thumbnail; clicking opens the full file from
   a local path.

4. **Given** a message that was edited or deleted, **When** the user views it, **Then**
   the message is marked as edited (with edit history accessible) or as deleted.

---

### User Story 5 — Live Real-Time Tracking (Priority: P5, Future)

A user wants to keep the archive continuously up to date without manual re-runs. A daemon
process maintains the authenticated session, listens for new messages, edits, and
deletions in real time, writes them to the database, and incrementally updates the
affected HTML pages.

**Why this priority**: Future enhancement. The architecture MUST accommodate it, but it
is out of scope for the current implementation.

**Independent Test**: Start the daemon, send a message in a monitored chat, wait ≤ 5
seconds, then open the HTML page for that chat and verify the new message appears.

**Acceptance Scenarios**:

1. **Given** the daemon is running, **When** a new message arrives in any monitored chat,
   **Then** the message is stored in the database within 5 seconds and the HTML for that
   chat is updated.

2. **Given** a previously stored message is edited on Telegram, **When** the daemon
   receives the edit event, **Then** the edit is recorded in the database and the HTML
   reflects the latest text with an edit marker.

3. **Given** a message is deleted on Telegram, **When** the daemon receives the deletion
   event, **Then** the message is marked as deleted in the database and the HTML shows
   a deletion placeholder.

---

### Edge Cases

- What happens when a dialog has zero messages (empty chat)?
- What happens when an attachment file is too large to download within available disk space?
- What happens when the Telegram API returns a FloodWait error mid-export?
- What happens when a message references a reply that was never fetched (deleted before export)?
- What happens when two different files have identical content (deduplication correctness)?
- What happens when the user's 2FA password is wrong on first authentication?
- What happens when the session file is corrupted or expired?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST authenticate a Telegram user account via MTProto using
  `api_id`, `api_hash`, phone number, confirmation code, and optional 2FA password.
- **FR-002**: The system MUST save the authenticated session locally so subsequent runs
  do not require re-authentication.
- **FR-003**: The system MUST enumerate all dialogs accessible to the account: personal
  chats, groups, supergroups, and channels (including private ones).
- **FR-004**: The system MUST support filtering dialogs by: whitelist of IDs or names,
  blacklist of IDs or names, dialog type (personal / group / channel).
- **FR-005**: The system MUST fetch the complete message history of each selected dialog,
  including: text content, service messages, reply-to chain, forwarded-from metadata,
  reactions (emoji + count), edit history (all versions with timestamps), and deletion
  flags.
- **FR-006**: The system MUST download all message attachments: photos, videos, voice
  messages, video notes (circles), documents, stickers, GIFs, and audio files.
- **FR-007**: The system MUST store downloaded attachments on the local filesystem,
  deduplicated by content hash, and record the local file path in the database.
- **FR-008**: The system MUST store all fetched data in a local SQLite database as the
  canonical, authoritative source for all output generation.
- **FR-009**: The system MUST maintain a per-chat watermark (cursor) recording the last
  successfully stored message ID so that subsequent runs fetch only new data.
- **FR-010**: The system MUST generate a static HTML archive consisting of: an index page
  listing all exported chats, and one page per chat with messages in chronological order.
- **FR-011**: Each chat HTML page MUST display for every message: sender name, timestamp,
  message text, inline media (photo thumbnail, video thumbnail with local src, audio
  player), reply quote, forwarded-from attribution, reaction counts, edit marker, and
  deletion marker.
- **FR-012**: The HTML archive MUST be fully self-contained and functional with no
  internet connection (all assets referenced via local relative paths).
- **FR-013**: The system MUST handle Telegram API rate limits by honouring FloodWait
  durations, applying exponential back-off on errors, and capping concurrent downloads.
- **FR-014**: The system MUST log progress to the console: current dialog, message fetch
  progress, download progress, and errors with clear descriptions.
- **FR-015**: The `render` (HTML generation) layer MUST be invokable independently from
  the local database without any network access.
- **FR-016** *(future)*: The architecture MUST support a live daemon mode that listens
  for real-time events and incrementally updates the database and HTML without a full
  re-fetch.

### Key Entities

- **Dialog**: A Telegram conversation entity — personal chat, group, supergroup, or
  channel. Attributes: Telegram ID, type, title/name, member count snapshot, export
  watermark.
- **Message**: A single unit of communication within a dialog. Attributes: Telegram
  message ID, dialog ID, sender ID, timestamp, text, reply-to message ID, forwarded-from
  (dialog ID + message ID), service-message type, current text, edit history entries,
  deletion flag, list of attachment IDs, list of reactions.
- **Attachment**: A media or document file associated with a message. Attributes:
  Telegram file ID, content hash, local file path, MIME type, file size, attachment
  category (photo / video / audio / voice / video-note / document / sticker / gif).
- **EditRecord**: A historical version of a message's text. Attributes: message ID,
  edit timestamp, text at that point.
- **Reaction**: An emoji reaction on a message. Attributes: message ID, emoji, count.
- **ExportCursor**: The per-dialog watermark. Attributes: dialog ID, last-fetched
  message ID, last-fetched timestamp.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can complete a first-time full export of an account with up to
  50 dialogs and 100,000 messages without manual intervention, from login through HTML
  generation.
- **SC-002**: An incremental re-run fetches zero duplicate messages and zero duplicate
  attachments compared to the previous run.
- **SC-003**: The HTML archive opens in a browser with no internet connection; all pages
  and inline media load within 2 seconds on a local filesystem.
- **SC-004**: A run interrupted at any point can be resumed and produces the same final
  result as an uninterrupted run (idempotent output).
- **SC-005**: When a Telegram FloodWait error occurs, the tool waits the required
  duration and continues automatically without losing already-stored progress.
- **SC-006**: Attachment deduplication ensures that the same file referenced by multiple
  messages is stored exactly once on disk.
- **SC-007**: The tool correctly represents edited messages (showing current text +
  edit history) and deleted messages (showing a placeholder) in the HTML archive.

## Assumptions

- The user runs the tool on their own personal machine and is the owner of the Telegram
  account being archived.
- Disk space sufficient to hold all attachments is available on the local machine.
- The tool is invoked from the command line; there is no graphical user interface.
- Credentials (`api_id`, `api_hash`) are obtained by the user from Telegram's developer
  portal before first use.
- The session file and database live in a single configurable data directory with
  filesystem permissions restricted to the running user (600/700).
- Pagination of large chat histories is handled transparently; no manual batching is
  required by the user.
- The HTML archive is generated for human reading in a modern desktop browser; mobile
  browser optimisation is out of scope for now.
- Live daemon mode (US5) is out of scope for the current implementation but the layer
  architecture MUST not prevent adding it later.

## Clarifications

### Session 2026-07-05

- Q: Output format preference → A: Static HTML only, no server-side backend required.
- Q: Storage backend preference → A: SQLite as the canonical database.
- Q: Attachment storage strategy → A: Media stored as individual files on disk; the
  database records only the local file path and content hash, never binary blobs.
- Q: Default history scope → A: Fetch the complete available history of every selected
  chat (no date cutoff by default).
- Q: Privacy / external export → A: Strictly local; no data leaves the machine, no
  upload or sharing features needed.
- Q: Live-mode scope → A: Out of scope for the first release; the database schema and
  layer boundaries MUST be designed to accommodate it without breaking changes.
