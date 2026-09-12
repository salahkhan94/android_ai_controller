"""Run the separate WhatsApp reply service or inspect Hinge without sending.

Server mode validates Twilio signatures and permits only the configured owner's
commands. Each selected reply requires a current version-bound WhatsApp choice.
Use inspect-list / inspect-history for read-only device checks without Twilio.
"""
import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import threading
from urllib.parse import urlparse
from llm_client import load_project_env
from match_replies.device import ROOT
from match_replies.hinge import Hinge


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['serve','inspect-list','inspect-history'])
    parser.add_argument('--match',help='Unique name for inspect-history')
    parser.add_argument('--port',type=int,default=8787)
    parser.add_argument('--udid',default='127.0.0.1:6555')
    parser.add_argument('--server',default='http://127.0.0.1:4723')
    parser.add_argument('--max-scrolls',type=int,default=80)
    args=parser.parse_args()
    if args.max_scrolls<1: parser.error('max-scrolls must be positive')
    load_project_env();load_project_env(ROOT/'.env.replies')
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('twilio').setLevel(logging.WARNING)
    hinge=Hinge(args.udid,args.server,args.max_scrolls)
    if args.command!='serve':
        matches=hinge.list_matches()
        if args.command=='inspect-list': print(json.dumps(matches,ensure_ascii=False,indent=2));return
        selected=[m for m in matches if m['name']==args.match]
        if len(selected)!=1: parser.error('Choose a unique --match from inspect-list')
        result=hinge.history(selected[0])
        print(f"Read {len(result['messages'])} entries; coverage: {result['coverage']}. Evidence saved under captures/matches.")
        return
    from match_replies.storage import Store
    from match_replies.controller import Controller
    from match_replies.drafts import generate
    from match_replies.web import create_app, worker
    from twilio.rest import Client
    from twilio.http.http_client import TwilioHttpClient
    from waitress import create_server
    required=['TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','WHATSAPP_OWNER','WHATSAPP_SENDER','WHATSAPP_WEBHOOK_URL','OPENAI_API_KEY']
    missing=[key for key in required if not os.environ.get(key)]
    if missing: parser.error('Set these in .env.replies (OpenAI may remain in .env): '+', '.join(missing))
    config={'token':os.environ['TWILIO_AUTH_TOKEN'],'owner':os.environ['WHATSAPP_OWNER'],
            'sender':os.environ['WHATSAPP_SENDER'],'url':os.environ['WHATSAPP_WEBHOOK_URL']}
    url=urlparse(config['url'])
    if url.scheme!='https' or url.path!='/whatsapp' or url.query:
        parser.error('WHATSAPP_WEBHOOK_URL must be the exact HTTPS URL ending /whatsapp, without a query')
    if not all(config[key].startswith('whatsapp:+') for key in ('owner','sender')):
        parser.error('WhatsApp numbers must use whatsapp:+countrycode... format')
    folder=ROOT/'captures'/'match_replies';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Entire service is single-owner/single-worker; prevents duplicate queue workers.
    with (folder/'service.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: parser.error('Reply service is already running')
        store=Store(folder/'state.sqlite3')
        controller=Controller(store,hinge,lambda h,c:generate(h,c,os.environ.get('OPENAI_MODEL','gpt-5.6-sol')))
        client=Client(os.environ['TWILIO_ACCOUNT_SID'],config['token'],http_client=TwilioHttpClient(timeout=30))
        def transport(body):
            return client.messages.create(from_=config['sender'],to=config['owner'],body=body,status_callback=config['url']+'/status').sid
        stopped=threading.Event()
        logging.basicConfig(level=logging.INFO)
        thread=threading.Thread(target=worker,args=(store,controller,transport,stopped),daemon=True)
        thread.start()
        server=create_server(create_app(store,config),host='127.0.0.1',port=args.port)
        print(f'Reply service listening on 127.0.0.1:{args.port}. Expose only this port via HTTPS, never Appium/ADB.',flush=True)
        try: server.run()
        finally: stopped.set();thread.join(timeout=10);server.close()

if __name__=='__main__':
    main()
