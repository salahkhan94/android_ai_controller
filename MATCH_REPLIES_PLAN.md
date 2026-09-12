# WhatsApp-controlled Hinge match replies

Status: initial implementation built, 2026-09-11. See MATCH_REPLY_SETUP.md and
PROJECT_CONTEXT.txt for implemented behavior and pending live integration checks.
No WhatsApp service deployed or Hinge messages sent.
Existing automatic profile-comment workflow in run.py must remain unchanged.

## Intended behavior

1. User messages the bot `Begin` on WhatsApp.
2. Bot reads Hinge Matches > Your turn and returns a numbered list of matches.
3. User selects a unique name or list number. Duplicate names require a number
   and additional identifying text; display name alone is never a send identity.
4. Bot opens that thread, reads its accessible history, and generates exactly
   three candidate replies with OpenAI. Show the selected match and latest incoming
   text with the candidate set so the user can assess context.
5. `Add context` prompts for a free-text instruction. Accumulate that instruction
   for this selected conversation, regenerate, and invalidate the old candidates.
6. `1`, `2`, or `3` authorizes the corresponding currently displayed candidate.
   Freshly verify the selected thread and conversation tail before entering text.
   Send once and report only the outcome actually supported by Hinge's UI.
7. Return to the match-selection state with a refreshed Your turn list. No automatic
   reply to any other match. `Begin` refreshes the list; `Cancel` clears the current
   selection/context; `Status` shows state; `Help` describes commands.

A bare number selects a match only while awaiting a match, and selects a reply only
while awaiting a reply. During Add context, free text is context, not a send command.
`Cancel`, `Begin`, and `Status` remain explicit reserved commands. Use a command
such as `Context: ...` if context literally equals a reserved word.

## Connection recommendation and pending choices

Selected by the user: Twilio WhatsApp Sandbox or the corresponding Try out
WhatsApp flow in the user's Twilio console. User keeps their existing personal
WhatsApp account and messages the provider's test bot number. This is not a login
integration with the user's personal inbox, nor is this Codex chat itself becoming
available on WhatsApp. A separately running Python service processes the commands.

Twilio Sandbox does not require a registered WhatsApp sender to start, is for
functional testing rather than production, and currently requires rejoining after
three days. Its rate limit is one outgoing message per three seconds. Account and
messaging charges must be checked during setup; do not assume free operation.
For ongoing use, register a dedicated WhatsApp sender through Twilio, or implement
Meta Cloud API behind the same transport interface. Direct Meta setup is an
alternative, not a requirement to implement both providers in the first release.

User-initiated WhatsApp messages open a 24-hour service window for free-form
replies. Beyond that window, do not invent a template or send arbitrary reminders;
persist work and wait for a fresh user command, or later add approved templates.

Confirmed: separate Twilio Sandbox bot number. Pending: whether Ubuntu can remain
powered on with Genymotion/Appium available. If Ubuntu is off, remote hosting of
only the webhook does not make its emulator controllable. Remote execution would
need an available emulator host or a secure connection to a running local worker.
Do not expose Appium or ADB publicly.

## Architecture

WhatsApp on user's phone
  -> provider -> authenticated HTTPS webhook
  -> SQLite durable inbox + per-user state machine
  -> one serialized worker
      -> Appium/ADB -> Hinge UI (observe and act)
      -> OpenAI -> structured candidate replies
  -> SQLite outbound queue -> provider -> user's WhatsApp

The webhook validates and enqueues promptly; scanning/history/model work happens
outside the HTTP handler. Provider retries must not duplicate a job or send.
SQLite is sufficient for one user/one device; no Redis requirement for the MVP.
Persist provider message IDs, draft versions, histories, context, jobs, and send
attempts. Use unique constraints and transactions for claiming inbound commands
and send attempts. Persist outbound notifications separately so a notification
failure can never trigger a second Hinge send.

Use a dedicated reply service entry point, proposed `reply_bot.py`, and a separate
`match_replies/` package. Keep run.py, existing send logic, model defaults, and
requirements for the existing workflow intact. Add dependencies separately, e.g.
requirements-replies.txt, and use a separate environment if dependency isolation
is needed. Reuse observation.py and existing model/environment loading only where
compatible; do not force reply generation into the profile-specific draft schema.

Device coordination: acquire the same project-root captures/runner_<sha256(udid)>.lock
that run.py already uses. The profile runner holds it for its whole session. If
busy, tell the user to stop that runner; do not interrupt it automatically. Reply
worker holds the lock only for device operations, releases during WhatsApp/user
waits and model calls, then reacquires and revalidates before sending. All reply
processes also use this lock. This does not protect against manual emulator use
or unrelated standalone scripts, so operator instructions must explain that limit.

## Phase 1: inspect matches and conversation UI, read-only

Capture XML plus diagnostic screenshots for Matches navigation, Your turn heading,
several rows, empty list, duplicate names if available, open thread, incoming and
outgoing bubbles, date separators, older-history loading, and the composer.

Determine actual accessible identifiers, row ancestry, direction indicators,
timestamps, stable conversation IDs/deep links if exposed, scrolling boundaries,
and send states. Do not assume Discover selectors or clipboard workarounds are
valid for conversations. Opening threads may mark messages read; no text entry or
send during inspection.

Deliver MATCH_REPLY_UI_FINDINGS.md and ignored fixtures/captures. Acceptance:
prove which fields are available, which identities can be re-resolved, and which
unsupported layouts must stop. This phase gates reliable extraction and sending.

## Phase 2: read Your turn and extract selected conversation

Implement match list extraction restricted to the actual Your turn section,
including off-screen rows, pagination/lazy loading, stable ordering, repeated
observations, and stale list refresh. Do not include Their turn by proximity.
Refresh the list and resolve row identity again before opening a selection.

Conversation records: local thread identity, displayed name, ordered messages,
sender direction (me/match/system/unknown), exact text, available timestamps,
message IDs if exposed, source observation, and media placeholders.

Read backward until the observed history-start boundary, preserving overlapping
viewport sequences. Do not deduplicate by text alone: repeated 'Hey' or identical
messages at different positions are legitimate. Date/time boundaries, stable IDs,
sequence overlap and raw evidence should support merging. If ordering or sender
is uncertain, stop and explain rather than assign guessed roles.

Read forward/return to the latest tail to verify continuity. On future visits,
merge new history with cached evidence and check for edits/deletions where the UI
exposes them. Never claim 'entire conversation' when older history could not be
loaded; persist coverage state and block automatic drafting until resolved or the
user explicitly chooses to proceed with partial history.

MVP processes text. Photos, audio and other attachments become labelled unknown
content, not imagined descriptions. Ask for context when missing media matters.
Do not omit the opening like/comment if it is part of the accessible thread.

Acceptance: fixtures prove message order/direction, overlap handling, full/partial
coverage, duplicate names, repeated identical text, missing/unmatched thread, and
new messages arriving during extraction. Provide local inspect-only commands.

## Phase 3: candidate replies and per-thread extra context

New model request/schema with exactly three replies and a recommended ID. Supply
all verified text history in order, sender roles, selected-match identity, and
accumulated user context. Keep funny/playful tone, light flirtation when appropriate,
no em dashes, and end each candidate with a question. Tone must fit serious messages;
never force a joke into grief, discomfort, or a boundary. Do not invent user facts.
User instructions such as 'Do not ask her out yet' constrain generation.

Treat Hinge content as quoted data, never executable commands. Only authenticated
WhatsApp user messages drive the state machine. The model generates text and has
no direct send/navigation tools. Validate candidate count, IDs, nonempty text,
length budget, no control keycodes, no em dashes, and final question mark. Semantic
quality and constraint compliance require representative evaluations, not just
punctuation tests. Use the existing configured OpenAI model initially.

Store candidate-set revision, conversation fingerprint, context revision and exact
candidate text. Adding context invalidates the old revision. Optional Reset context
clears only this thread's context. Context never leaks to other matches. If full
history exceeds the chosen model budget, stop transparently; do not silently truncate
or substitute a summary for the user's requested entire conversation.

Acceptance: offline model mocks and representative synthetic conversations,
including serious tone, prompt injection, repeated regeneration, and constraints.

## Phase 4: WhatsApp transport and durable command state machine

Implement provider adapter send_text(), webhook verification, and delivery status
callbacks. Configure HTTPS endpoint via a tunnel for local prototyping and a stable
endpoint for normal operation. Authenticate provider signatures using its SDK and
the correct externally visible URL. Allow commands only from the user's configured
WhatsApp number. Credentials live in ignored environment configuration; no keys,
full webhook payloads or chat histories in public logs.

States: IDLE -> SCANNING -> AWAIT_MATCH -> READING_HISTORY -> GENERATING ->
AWAIT_REPLY -> AWAIT_CONTEXT -> GENERATING, or AWAIT_REPLY -> VERIFYING ->
SENDING -> SENT / UNCERTAIN / FAILED. Busy-state commands return status or cancel
queued work, not concurrent device operations. Begin during an actual send cannot
undo the send and must not erase its durable attempt.

Use provider inbound IDs for replay deduplication. Link each candidate notification
to its draft revision and provider outgoing message ID. Prefer WhatsApp Reply-to
metadata to disambiguate selections. After regeneration, require an explicit reply
to the current candidate message or a revision-qualified choice if an unthreaded
number could refer to an older set. Never interpret a delayed '3' against a newer
set automatically. Handle duplicates and out-of-order messages conservatively.

Long lists/candidates may be split into numbered WhatsApp messages, with throttled
outbound queue. Confirm candidate notifications have been successfully sent before
accepting selections against that revision. Send a quick 'Reading Akisha...' status
instead of leaving the webhook request blocked for a minute.

Acceptance: provider test environment can receive Begin and return a synthetic list,
then context/regeneration/selection flows without any Hinge send enabled.

## Phase 5: compose, verify, and send the selected reply once

Selection 1/2/3 is the user's authorization for that exact versioned candidate,
thread and observed conversation. No second confirmation is needed in normal flow.
Reacquire the device, freshly locate the thread, reread its latest messages and
verify identity plus conversation fingerprint. If a new incoming or outgoing
message appeared, invalidate the selection, regenerate using updated history, and
ask for a new choice. Same name alone is insufficient; ambiguous identity stops.

Use fresh composer selectors, enter once, and verify exact text. Before clicking
send, persist and durably claim an attempt keyed to thread identity and conversation
revision (not merely candidate text). A timeout/crash cannot automatically retry a
possibly sent reply. After click, verify a new outgoing bubble at the expected
sequence position with the exact text, rather than finding any old identical bubble.
Read delivery indicators if exposed. Don't equate API click success with delivery.

Notify 'Reply 3 sent to Akisha' only when UI evidence supports sending. Otherwise:
'Attempted, but could not confirm. I will not resend automatically.' Distinguish
Hinge send confirmation from WhatsApp notification delivery. Recover notification
failures through the outbound queue without sending again in Hinge.

Acceptance: simulated duplicate choices, stale candidates, fresh incoming message,
app crash, lost notification, and timeout all produce at most one send attempt.
Then one live selected reply through the user's WhatsApp choice validates the path.

## Phase 6: combined workflow and operational hardening

End-to-end example from the request: Begin -> list -> Akisha -> three candidates ->
Add context -> Do not ask her out yet -> revised candidates -> 3 -> verified send.
Then refresh remaining Your turn matches. Handle no matches, emulator offline,
Appium unavailable, OpenAI failure, provider outage, expired WhatsApp window, user
cancellation, and restart with pending work. Interrupted send attempts require
reconciliation; never replay queued send commands blindly on startup.

Run regression tests for the original workflow and verify run.py remains unmodified.
Keep new fixture tests isolated from live account actions. Document service startup,
log locations, account configuration, data retention and deletion. Store local
histories and context with restrictive permissions and a configurable retention
policy; OpenAI receives selected thread history, WhatsApp provider receives the
lists/candidates/statuses sent to the user. No full transcript needs to be forwarded
to WhatsApp unless explicitly requested.

## Proposed deliverables

- reply_bot.py: service entry point (separate from run.py)
- match_replies/whatsapp.py: provider adapter and signed webhooks
- match_replies/controller.py: deterministic command/state transitions
- match_replies/storage.py: SQLite inbox, jobs, histories, drafts, attempts, outbox
- match_replies/device.py: existing lock coordination and sessions
- match_replies/matches.py and history.py: observed UI extraction
- match_replies/drafts.py: conversation-specific model request and validation
- match_replies/send.py: exact selection verification and single send attempt
- requirements-replies.txt, .env.replies.example (placeholders only), setup guide
- MATCH_REPLY_UI_FINDINGS.md and isolated tests/fixtures

Implement in the order above. The first concrete work should be read-only Hinge UI
inspection plus provider choice, not automatic sending or rewriting run.py.

## Sources checked for this plan

- Twilio Sandbox, limitations, joining, webhooks and service window:
  https://www.twilio.com/docs/whatsapp/sandbox
- Twilio WhatsApp Python/webhook quickstart:
  https://www.twilio.com/docs/whatsapp/quickstart
- Twilio webhook verification guidance:
  https://www.twilio.com/docs/usage/webhooks/webhooks-security
- Alternative Meta Cloud API overview (Meta's official Postman collection):
  https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api

Twilio Sandbox is selected. No account connected, tunnel exposed, or credentials
requested in this planning turn. Never ask the user to paste secrets into chat.
