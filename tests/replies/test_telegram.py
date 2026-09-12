"""Verify Telegram owner isolation, replay handling, pairing and secret-safe errors."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError
from match_replies.storage import Store
from match_replies.telegram import Client, TelegramError, accept_update, pair, poll


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db')
        self.update = {'update_id': 1, 'message': {
            'message_id': 8, 'date': 100, 'text': '/start',
            'chat': {'id': 42, 'type': 'private'}, 'from': {'id': 42}}}

    def accept(self):
        return accept_update(self.store, self.update, 42, 42, 99, 90)

    def test_owner_private_chat_dedup_and_alias(self):
        self.assertTrue(self.accept())
        self.assertFalse(self.accept())
        self.assertEqual(self.store.claim('inbox')['body'], 'Help')
        for kind in ('other', 'group', 'old', 'edited'):
            with self.subTest(kind=kind):
                u = json.loads(json.dumps(self.update)); u['update_id'] = 2
                if kind == 'other': u['message']['from']['id'] = 43
                if kind == 'group': u['message']['chat']['type'] = 'group'
                if kind == 'old': u['message']['date'] = 1
                if kind == 'edited': u['edited_message'] = u.pop('message')
                self.assertFalse(accept_update(self.store, u, 42, 42, 99, 90))

    def test_reply_binding_requires_our_bot(self):
        m = self.update['message']; m['text'] = '2'
        m['reply_to_message'] = {'message_id': 7, 'from': {'id': 99}}
        self.assertTrue(self.accept())
        self.assertEqual(self.store.claim('inbox')['reply_to'], '7')
        self.update['update_id'] = 2
        m['reply_to_message']['from']['id'] = 100
        self.assertTrue(self.accept())
        self.assertEqual(self.store.claim('inbox')['reply_to'], '')

    def test_restart_preserves_attempts_but_invalidates_pending_choices(self):
        self.store.claim_send('a', {'reply': 'One?'})
        self.store.accept('old', 'code 2')
        self.store.finish('previous', {'mode': 'reply', 'revision': 'code'}, ['One?'])
        self.store.reset_session()
        self.assertFalse(self.store.claim_send('a', {}))
        self.assertEqual(self.store.state(), {'mode': 'idle'})
        self.assertIsNone(self.store.claim('inbox'))
        self.assertIsNone(self.store.claim('outbox'))

    def test_poll_persists_offset_after_accept_and_replay_is_deduplicated(self):
        client = Mock(); stopped = Mock(); stopped.is_set.side_effect = [False, False, True]
        client.updates.return_value = [self.update]
        poll(client, self.store, 42, 42, 99, stopped, 90)
        self.assertEqual(self.store.setting('telegram_offset:99'), 2)
        self.assertFalse(self.accept())
        self.assertIsNotNone(self.store.claim('inbox'))

    def test_pair_requires_code_and_preserves_token(self):
        env = Path(self.tmp.name) / '.env.replies'
        env.write_text('TELEGRAM_BOT_TOKEN=secret\n')
        wrong = json.loads(json.dumps(self.update)); wrong['message']['text'] = '/pair wrong'
        valid = json.loads(json.dumps(self.update)); valid['update_id'] = 2
        valid['message']['text'] = '/pair matching-code'
        client = Mock(); client.updates.return_value = [wrong, valid]
        with patch('match_replies.telegram.secrets.token_hex', return_value='matching-code'), patch('match_replies.telegram.time.time', return_value=90), patch('builtins.print'):
            pair(client, self.store, 99, env)
        from dotenv import dotenv_values
        config = dotenv_values(env)
        self.assertEqual(config['TELEGRAM_BOT_TOKEN'], 'secret')
        self.assertEqual(config['TELEGRAM_OWNER_ID'], '42')
        self.assertEqual(config['TELEGRAM_CHAT_ID'], '42')

    def test_network_errors_never_expose_token_or_retry_send(self):
        client = Client('private-token')
        with patch('match_replies.telegram.urlopen', side_effect=URLError('https://api.telegram.org/botprivate-token/sendMessage')) as request:
            with self.assertRaises(TelegramError) as caught: client.send(42, 'Hi')
            self.assertNotIn('private-token', str(caught.exception))
            request.assert_called_once()

    def test_existing_webhook_stops_polling_setup(self):
        client = Client('private-token')
        client.call = Mock(side_effect=[{'id': 99}, {'url': 'https://existing.test'}])
        with self.assertRaisesRegex(TelegramError, 'already has a webhook'): client.check()
