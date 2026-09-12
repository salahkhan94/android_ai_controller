# Telegram-controlled Hinge match replies

Current design: Telegram polling replaces the previous Twilio/WhatsApp transport.
The automatic profile-comment pipeline in run.py stays unchanged. Setup instructions
are in MATCH_REPLY_SETUP.md. The user must select each match reply explicitly.

## Workflow

Phone: Begin -> numbered Your turn list -> choose match -> full accessible history
-> three funny, appropriately flirtatious candidate replies ending in questions
-> optional Add context and regeneration -> explicit selection -> fresh history
check -> one Hinge send attempt -> report observed result. No em dashes in replies.
After completion, Begin refreshes the list. There is no automatic reply to every match.

## Architecture

Telegram phone app -> Telegram Bot API getUpdates -> owner/private-chat filter
-> persistent inbox and polling offset -> single controller worker
-> shared emulator lock -> Appium -> Hinge list/history/composer
-> OpenAI generation with history and accumulated user context
-> persistent outbox -> Telegram sendMessage -> paired phone account.

The poller continues receiving while the worker scans Hinge. No public webhook or
tunnel is needed. Tokens stay in ignored .env.replies; model credentials stay in .env.
The Python service is separate from this Codex conversation.

## Implemented components and phases

1. Device session: same nonblocking per-device flock used by run.py. Device operations
   hold it; waiting for the user and generation release it. Hinge must be foreground.
2. Your turn extraction: bounded scrolling and section detection; preserve preview
   plus name as row identity. Duplicate names stop instead of guessing. Pagination
   is implemented but the live verification used a one-row list.
3. History: re-resolve the row before opening; explicit speaker labels, visual order,
   reaction metadata, dates and opening context. Scroll to an observed beginning and
   merge overlapping views to the current tail. Missing or ambiguous overlaps,
   unknown attachment formats, changed identity, and incomplete coverage stop.
   UI boundaries do not prove all inaccessible server history was retrieved.
4. Generation: exactly three distinct replies, full captured history and accumulated
   context, no em dashes, bounded length, question endings. Conversation content is
   untrusted model input, never a command source. Oversized context stops rather
   than silently truncating it.
5. Telegram: standard-library HTTPS adapter, secret-safe errors, getMe/webhook checks,
   expiring local pairing code, numeric owner and private chat binding, durable
   update IDs and offsets. Only text message updates accepted; edits and groups
   ignored. Slash commands map to controller commands.
6. State and approval: idle/match/context/reply states. Each candidate set has a new
   revision. A reply choice must quote a message sent by this bot belonging to the
   current revision, or include the exact revision code. All candidate chunks must
   have dispatched. Old choices cannot approve regenerated replies. Bare numbers
   only select matches; replying to candidates or including their code authorizes send.
7. Submission: reread selected thread, regenerate if history changed, refuse existing
   drafts, enter selected text once, verify exact content, claim a durable attempt
   immediately before the only Send click. Observe a new outgoing bubble to report
   success; uncertain outcomes never trigger automatic resends.

## Persistence, lifecycle and errors

SQLite captures/match_replies/state.sqlite3 contains inbox, outbox, state, settings
and send attempts. A shared service lock excludes concurrent pairing/service runs.
The poll offset advances only after accepted updates are committed; inbox uniqueness
handles replay between those transactions. Unauthorized updates also advance the
offset without executing anything.

Startup resets selection and pending notifications while preserving deduplication
and send attempts, including old-provider attempts. Commands predating startup are
ignored. User sends Begin again after restart. Polling retries after connection
errors; Telegram sends are never retried automatically after uncertain HTTP outcomes.
Ctrl+C stops polling and waits for an in-flight command to record its outcome.
Cancel can remove queued choices, but cannot undo a send already in progress.

API acceptance does not mean a Telegram message was read. Hinge success is an
observed exact outgoing bubble, not remote delivery confirmation. Logs avoid tokens
and raw HTTP URLs. Captured histories remain local; OpenAI receives selected history
and context, Telegram receives the list/latest incoming/candidates/status messages.

## Files

- reply_bot.py: pair, serve, inspect-list and inspect-history CLI.
- match_replies/telegram.py: HTTPS adapter, pairing, owner filtering, polling.
- match_replies/worker.py: command processing and outbound notifications.
- match_replies/controller.py: selection, context, candidate binding and send flow.
- match_replies/storage.py: durable state, offsets, deduplication and attempts.
- match_replies/device.py: shared emulator lock and Appium session.
- match_replies/hinge.py: UI reading and guarded reply entry/send.
- match_replies/drafts.py: model request and output validation.
- tests/replies/: controller, UI parsing, send guards and Telegram tests.

## Verification and remaining work

21 reply tests and 65 original pipeline tests pass after migration. Tests cover
owner/group rejection, update replay, offline commands, pairing, token-safe errors,
reply-to binding, old candidate rejection, changed histories and send-attempt claims.
Earlier live read-only inspection found one Your turn match and 52 history entries.
Remaining live gates: pair phone, Help round trip, Begin and generation via Telegram,
then a user-selected Hinge reply to validate composer/Send behavior. Do not represent
these gates as completed merely because offline tests pass.

Transport reference: https://core.telegram.org/bots/api

Telegram connection check: getMe successfully verified @sal_ai_helper_bot and
getWebhookInfo confirmed no webhook. No messages sent by this check. Phone pairing
and the Help round trip remain pending.
