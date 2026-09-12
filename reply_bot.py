"""Pair or run the private Telegram Hinge reply bot using outbound polling.

Run pair once on your laptop, then serve. Inspect commands read Hinge without
Telegram. Every match reply requires a current, explicitly selected candidate.
"""
import argparse
import fcntl
import json
import logging
import os
import threading
import time
from llm_client import load_project_env
from match_replies.device import ROOT
from match_replies.hinge import Hinge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['pair', 'serve', 'inspect-list', 'inspect-history'])
    parser.add_argument('--match', help='Unique name for inspect-history')
    parser.add_argument('--udid', default='127.0.0.1:6555')
    parser.add_argument('--server', default='http://127.0.0.1:4723')
    parser.add_argument('--max-scrolls', type=int, default=80)
    args = parser.parse_args()
    if args.max_scrolls < 1: parser.error('--max-scrolls must be positive')
    load_project_env()
    load_project_env(ROOT / '.env.replies')
    logging.basicConfig(level=logging.INFO)
    hinge = Hinge(args.udid, args.server, args.max_scrolls)
    if args.command.startswith('inspect-'):
        matches = hinge.list_matches()
        if args.command == 'inspect-list':
            print(json.dumps(matches, ensure_ascii=False, indent=2))
            return
        selected = [m for m in matches if m['name'] == args.match]
        if len(selected) != 1: parser.error('Choose a unique --match from inspect-list')
        result = hinge.history(selected[0])
        print(f"Read {len(result['messages'])} entries; coverage: {result['coverage']}. Evidence under captures/matches.")
        return
    from match_replies.storage import Store
    from match_replies.controller import Controller
    from match_replies.drafts import generate
    from match_replies.telegram import Client, TelegramError, pair, poll
    from match_replies.worker import worker
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token: parser.error('Set TELEGRAM_BOT_TOKEN in .env.replies')
    folder = ROOT / 'captures' / 'match_replies'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (folder / 'service.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: parser.error('Reply service or pairing is already running')
        client = Client(token)
        try:
            me = client.check()
            store = Store(folder / 'state.sqlite3')
            if args.command == 'pair':
                pair(client, store, me['id'], ROOT / '.env.replies')
                return
            try:
                owner = int(os.environ.get('TELEGRAM_OWNER_ID', ''))
                chat = int(os.environ.get('TELEGRAM_CHAT_ID', ''))
                if owner <= 0 or chat != owner: raise ValueError()
            except ValueError:
                parser.error('Run reply_bot.py pair first to configure your private Telegram account')
            if not os.environ.get('OPENAI_API_KEY'): parser.error('Set OPENAI_API_KEY in .env')
            # Never carry queued notifications or approvals across restarts or users.
            store.reset_session()
            started = int(time.time())
            stopped = threading.Event()
            controller = Controller(store, hinge, lambda h, c: generate(h, c, os.environ.get('OPENAI_MODEL', 'gpt-5.6-sol')))
            thread = threading.Thread(target=worker, args=(store, controller, lambda body: client.send(chat, body), stopped))
            thread.start()
            print(f"Telegram bot @{me['username']} running. Send Help or Begin on your phone. Ctrl+C stops.", flush=True)
            try:
                poll(client, store, owner, chat, me['id'], stopped, started)
            finally:
                stopped.set()
                # Let an in-flight device action finish recording its outcome.
                print('Stopping; waiting for any current command to finish.', flush=True)
                thread.join()
        except TelegramError as exc:
            parser.error(str(exc))
        except KeyboardInterrupt:
            print('Stopped.', flush=True)


if __name__ == '__main__':
    main()
