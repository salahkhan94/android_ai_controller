# Telegram match reply bot setup

Telegram replaces the old WhatsApp integration. `run.py` remains unchanged.
Install Telegram on your phone; Telegram Desktop is optional. No Twilio account,
public server, webhook URL, tunnel, or Telegram Premium is needed for this bot.
OpenAI API usage is still billed separately.

## 1. Phone: create and open your bot

This is already done for @sal_ai_helper_bot. Open https://t.me/sal_ai_helper_bot
and tap Start. A response requires the Python service to be running.
For a new bot, use https://t.me/BotFather and `/newbot`.

## 2. Laptop: configuration and dependencies

The token is already saved in ignored `.env.replies`. Do not overwrite it with the
example file. Configuration looks like:

```dotenv
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_OWNER_ID=
TELEGRAM_CHAT_ID=
```

The pairing command fills the last two values automatically. OpenAI credentials
and OPENAI_MODEL remain in `.env`. Exported environment variables take precedence.
There are no Twilio/WhatsApp settings in the new example or active configuration.

From the project directory, install dependencies if needed:

```bash
python3 -m venv .venv-replies
.venv-replies/bin/python -m pip install -r requirements-replies.txt
```

The existing `.venv-replies` is already usable. The Telegram adapter uses Python's
standard library; no Telegram SDK is required.

## 3. Laptop and phone: pair once

On the laptop, run:

```bash
.venv-replies/bin/python reply_bot.py pair
```

The terminal prints `/pair` followed by a random code. Within five minutes, send
that entire message to YOUR bot on your phone (not to BotFather). The terminal
confirms pairing and saves your numeric user/chat IDs in `.env.replies`. A token
alone does not authorize the first stranger who messages the bot. Only the private
account that supplies this local code is paired. Stop the service before re-pairing.

Pairing does not access Hinge, call OpenAI, or send a Hinge reply.

## 4. Laptop: start the service

Keep Ubuntu awake with internet access. Leave Genymotion and Appium running and
Hinge open. Stop `run.py` before asking the reply bot to access Hinge; both use the
same device lock.

```bash
.venv-replies/bin/python reply_bot.py serve
```

Keep that terminal running. Ctrl+C stops polling and waits for any current device
command to finish recording its outcome. A send already underway may complete.
Do not run the old tunnel. No incoming port is opened by this service.

## 5. Phone: use the bot

1. Send `Help` to test messaging without accessing Hinge.
2. Send `Begin` to list matches in Your turn.
3. Send a unique name or list number to read the conversation and generate replies.
4. To revise, send `Add context`, then your instruction, e.g. `Do not ask her out yet`.
5. To send, use Telegram's Reply action on the CURRENT candidate message and type
   `1`, `2`, or `3`. Alternatively send the displayed code plus choice, e.g.
   `abc123def0 3`. A bare number without Reply does not approve a candidate.
6. After sending, use `Begin` to refresh the list. `Cancel` clears the selection;
   `Status` shows the current stage. `/start`, `/help`, `/begin`, `/cancel`, and
   `/status` are also accepted.

Only the paired private user/chat is accepted; groups, other users, edits, and
commands sent while the service was offline are ignored. On restart, send Begin
again: old state and pending notifications are invalidated. The send-attempt ledger
is retained, including during migration from the old provider.

## Recovery and verification

Persistent state is `captures/match_replies/state.sqlite3`. Never delete it to
retry a reply: it contains duplicate-send protection. Network polling retries;
message sends with uncertain outcomes are not automatically replayed. A successful
Telegram API send means accepted by Telegram, not proof you read it. Hinge send
success means an exact new outgoing bubble was observed, not proof of delivery.

```bash
.venv-replies/bin/python -m unittest discover -s tests/replies -q
.venv/bin/python -m unittest discover -s tests -q
.venv-replies/bin/python reply_bot.py inspect-list
.venv-replies/bin/python reply_bot.py inspect-history --match Akisha
```

Inspect commands do not use Telegram or OpenAI; opening a thread may mark it read.
Live Hinge list/history parsing was previously checked on one match and 52 history
entries. Duplicate names stop, attachment interpretation is unsupported, and history
coverage is based on UI boundaries. Actual selected Hinge reply sending still needs
live validation. Telegram pairing and a real phone round trip are pending until
performed; offline tests do not establish that they have happened.

References: https://core.telegram.org/bots/tutorial and
https://core.telegram.org/bots/api#getupdates

Telegram connection check: getMe successfully verified @sal_ai_helper_bot and
getWebhookInfo confirmed no webhook. No messages sent by this check. Phone pairing
and the Help round trip remain pending.

Send-button fix: live composer inspection exposed resource ID
co.hinge.app:id/sendMessageButton with description Send message, rather than Send.
Selector now requires the unique enabled clickable button. The microphone overlaps
its bounds, so the container is not a send target. Read-only evidence: captures/send_inspection/20260912T033542_318178Z_bd59ce67
23 reply tests and 65 profile tests pass. No draft sent during diagnosis. Restart
the bot to load the fix; inspect and clear any leftover unsent draft before Begin
and a new candidate approval. Actual send confirmation remains to be verified.
