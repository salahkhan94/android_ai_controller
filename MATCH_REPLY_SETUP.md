# Match reply bot setup

The new service is separate from run.py. Its dependencies are installed in
`.venv-replies`; existing `.venv` and profile scripts were not changed.

## 1. Join the WhatsApp test environment

Create/sign into Twilio. In Messaging choose **Try out WhatsApp** (or **Try
WhatsApp** in the legacy Console). Accept the Sandbox terms. Scan its QR code or
send its displayed `join ...` message from your personal WhatsApp to the displayed
number. Wait for the join confirmation. Use the number/code shown in YOUR console.

Documentation: https://www.twilio.com/docs/whatsapp/sandbox

## 2. Configure locally

Copy `.env.replies.example` to `.env.replies`. The latter is Git-ignored. Fill:

- TWILIO_ACCOUNT_SID: Account SID from Twilio
- TWILIO_AUTH_TOKEN: Auth Token from Twilio; keep secret
- WHATSAPP_OWNER: your personal number, e.g. whatsapp:+15551234567
- WHATSAPP_SENDER: Sandbox number, in whatsapp:+... format
- WHATSAPP_WEBHOOK_URL: exact public HTTPS URL ending /whatsapp

Keep the existing OpenAI credentials/model in `.env`. No keys belong in chat,
source code, screenshots, or public logs. Environment variables override files.

## 3. Start the local service

Stop run.py first, leave Hinge open, and keep Ubuntu, Genymotion and Appium running.
The shared device lock prevents concurrent emulator control.

```bash
.venv-replies/bin/python reply_bot.py serve
```

The server binds to 127.0.0.1:8787, not a public interface. Expose ONLY this port
through an HTTPS tunnel or your reverse proxy. Configure the tunnel's public URL
plus `/whatsapp` as WHATSAPP_WEBHOOK_URL and restart the service after URL changes.
Never expose ports 4723 (Appium), 5037 (ADB), or the emulator debugging port.

In Twilio's **When a message comes in** webhook setting, choose POST and paste the
same exact HTTPS /whatsapp URL. The service checks signatures against that URL,
including the original form parameters, and rejects other WhatsApp senders.
`GET /health` returns only an availability response.

Cloudflared is installed locally at `.venv-replies/bin/cloudflared`.
Start a temporary tunnel in a separate terminal:

```bash
.venv-replies/bin/cloudflared tunnel --url http://127.0.0.1:8787 --no-autoupdate
```

Each new tunnel gets a new address. Copy its HTTPS address plus `/whatsapp` into
`.env.replies` and Twilio's Inbound custom webhook setting (POST), then start or
restart the reply service. Keep both processes running. The current URL is in
`.env.replies`; do not assume a URL from an earlier session still works.

Setup on 2026-09-11: tunnel connected, service started, public `/health` returned
status ok, and an unsigned POST to `/whatsapp` returned 403. Twilio Console webhook
configuration and a real incoming WhatsApp command still need verification.

## 4. Commands

- Begin: enumerate Your turn matches.
- A unique match name or its number: read the thread and generate three replies.
- Add context: enter an instruction, then receive a new candidate set.
- In WhatsApp, Reply directly to the candidate notification with 1, 2, or 3.
- Alternatively send the displayed revision code and choice, e.g. abc123def0 3.
- Cancel: clear selection; Begin starts over.
- Status / Help: inspect the current command state.

Bare numbers without WhatsApp reply-to metadata are NOT send authorization. This
prevents a delayed old choice selecting a new draft after Add context. During
context entry, text is context and never an implicit send command.

The bot verifies the selected thread and history again immediately before entry.
A new message invalidates candidates. It claims a persistent send attempt before
one send click. Uncertain attempts cannot be automatically repeated. A sent result
means a new exact outgoing bubble was observed, not proof the match read it.

## Diagnostics and tests

```bash
.venv-replies/bin/python reply_bot.py inspect-list
.venv-replies/bin/python reply_bot.py inspect-history --match Akisha
.venv-replies/bin/python -m unittest discover -s tests/replies -q
.venv/bin/python -m unittest discover -s tests -q
```

Read-only diagnostics do not require Twilio credentials or call OpenAI. Opening a
thread may mark it read. XML/screenshots and conversation.json live in ignored
captures/matches. Persistent queue/state/attempts are in
captures/match_replies/state.sqlite3; directory and database have restrictive modes.
Deleting that database also deletes duplicate-send protection: do not delete it
as a retry workaround. There is no automatic retention deletion in this first version.

## Current limitations / verification gates

- Twilio account, join, credentials and public webhook must be configured by you.
- The live list parser was verified with one Your turn match. Pagination is
  implemented but requires live verification on a multi-page list.
- Duplicate display names currently stop instead of guessing a thread identity.
- Message speaker labels are explicit in observed XML. Opening-card content is
  preserved as opening_context rather than inventing an unlabelled sender.
- History coverage is a UI-boundary heuristic, not a guarantee of server history.
  Missing overlap, unknown history start, or scroll limit stops generation.
- Full attachment interpretation and inaccessible/deleted messages are unsupported.
- Reply text entry and the final Send control still require live validation with
  a user-selected candidate. Unknown controls leave an unsent draft and stop.
- Twilio test environment rate/windows apply. Outside the service window, queued
  notifications wait for another incoming user command. Provider send timeouts
  are marked uncertain rather than automatically replayed.
- After restart, an interrupted command/notification is not automatically replayed.
- Signed WhatsApp status callbacks update delivery state at /whatsapp/status.
  Provider outages may require Status/Begin after recovery.
- No OpenAI request or Hinge reply was sent during initial implementation tests.
