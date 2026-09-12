"""Telegram HTTPS polling, private-owner filtering and explicit local pairing.

Never log request URLs: Telegram embeds the bot token in their path. Incoming
updates are durably accepted before advancing the polling offset. Sends are not
retried after errors because Telegram may already have accepted the message.
"""
import json
import logging
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


class Client:
    def __init__(self, token):
        self.token = token

    def call(self, method, **payload):
        request = Request('https://api.telegram.org/bot' + self.token + '/' + method,
                          data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=40) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise TelegramError(f'Telegram HTTP {exc.code}; request not retried') from None
        except (URLError, OSError, ValueError):
            raise TelegramError('Telegram connection or response error; request not retried') from None
        if not result.get('ok'):
            raise TelegramError('Telegram rejected the request')
        return result['result']

    def updates(self, offset):
        return self.call('getUpdates', offset=offset, timeout=25,
                         allowed_updates=['message'])

    def send(self, chat, body):
        result = self.call('sendMessage', chat_id=chat, text=body,
                           link_preview_options={'is_disabled': True})
        return str(result['message_id'])

    def check(self):
        me = self.call('getMe')
        if self.call('getWebhookInfo').get('url'):
            raise TelegramError('This bot already has a webhook. Remove it before polling.')
        return me


def private_message(update):
    message = update.get('message', {})
    user = message.get('from', {})
    chat = message.get('chat', {})
    if chat.get('type') != 'private' or user.get('is_bot') or not user.get('id'):
        return None
    if chat.get('id') != user['id']:
        return None
    if not isinstance(message.get('text'), str):
        return None
    return message


def accept_update(store, update, owner, chat, bot_id, started):
    message = private_message(update)
    if not message or message['from']['id'] != owner or message['chat']['id'] != chat:
        return False
    # Ignore commands sent while the service was offline, including old approvals.
    if message.get('date', 0) < started:
        return False
    body = message['text'].strip()
    if not body or len(body) > 4000:
        return False
    aliases = {'/start': 'Help', '/help': 'Help', '/begin': 'Begin',
               '/cancel': 'Cancel', '/status': 'Status'}
    body = aliases.get(body.casefold(), body)
    replied = message.get('reply_to_message', {})
    reply_to = ''
    if replied.get('from', {}).get('id') == bot_id:
        reply_to = str(replied.get('message_id', ''))
    return store.accept(f'tg:{bot_id}:{update["update_id"]}', body, reply_to)


def poll(client, store, owner, chat, bot_id, stopped, started):
    key = f'telegram_offset:{bot_id}'
    offset = store.setting(key, 0)
    while not stopped.is_set():
        try:
            updates = client.updates(offset)
        except TelegramError as exc:
            log.warning('%s; polling resumes in five seconds', exc)
            stopped.wait(5)
            continue
        for update in updates:
            if stopped.is_set(): return
            accept_update(store, update, owner, chat, bot_id, started)
            offset = update['update_id'] + 1
            store.set_setting(key, offset)


def pair(client, store, bot_id, env_path):
    """Bind only a private user who knows the expiring code shown on this laptop."""
    from dotenv import set_key
    code = secrets.token_hex(12)
    started = int(time.time())
    deadline = time.monotonic() + 300
    key = f'telegram_offset:{bot_id}'
    offset = store.setting(key, 0)
    print('On your phone, open your bot and send this exact message within 5 minutes:', flush=True)
    print('/pair ' + code, flush=True)
    while time.monotonic() < deadline:
        for update in client.updates(offset):
            offset = update['update_id'] + 1
            store.set_setting(key, offset)
            m = private_message(update)
            if not m or m.get('date', 0) < started: continue
            if m['text'].strip() != '/pair ' + code: continue
            set_key(str(env_path), 'TELEGRAM_OWNER_ID', str(m['from']['id']))
            set_key(str(env_path), 'TELEGRAM_CHAT_ID', str(m['chat']['id']))
            env_path.chmod(0o600)
            store.reset_session()
            print('Paired your private Telegram account. Run reply_bot.py serve next.', flush=True)
            return
    raise TelegramError('Pairing expired. Run the pair command again.')
