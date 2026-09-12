"""Authenticated Twilio webhook and durable background worker; no public ADB."""
import logging
import time
from flask import Flask, request, Response
from twilio.request_validator import RequestValidator

log=logging.getLogger(__name__)

def create_app(store, config):
    app=Flask(__name__)
    app.config['MAX_CONTENT_LENGTH']=65536
    validator=RequestValidator(config['token'])
    @app.post('/whatsapp')
    def incoming():
        if not validator.validate(config['url'],request.form,request.headers.get('X-Twilio-Signature','')):
            return Response('Forbidden',403)
        if request.form.get('From')!=config['owner'] or request.form.get('To')!=config['sender']:
            return Response('Forbidden',403)
        sid=request.form.get('MessageSid','');body=request.form.get('Body','')
        if not sid or not body.strip() or len(body)>5000:
            return Response('<Response/>',200,mimetype='text/xml')
        store.accept(sid,body,request.form.get('OriginalRepliedMessageSid',''))
        return Response('<Response/>',200,mimetype='text/xml')
    @app.post('/whatsapp/status')
    def status_callback():
        if not validator.validate(config['url']+'/status',request.form,request.headers.get('X-Twilio-Signature','')):
            return Response('Forbidden',403)
        status=request.form.get('MessageStatus','')
        if status in ('queued','sent','delivered','read','failed','undelivered'):
            store.delivery(request.form.get('MessageSid',''),status)
        return Response('',204)
    @app.get('/health')
    def health(): return {'status':'ok'}
    return app

def worker(store, controller, transport, stopped):
    store.recover()
    last_send=0
    while not stopped.is_set():
        # Deliver pending output before accepting choices against a revision.
        outbound=store.claim('outbox') if store.window_open() else None
        if outbound:
            stopped.wait(max(0,3.1-(time.monotonic()-last_send)))
            try:
                sid=transport(outbound['body'])
                store.notification(outbound['id'],'sent',sid)
            except Exception as exc:
                # HTTP timeout may already have sent; do not blindly replay.
                store.notification(outbound['id'],'uncertain')
                log.error('WhatsApp notification uncertain (%s); no automatic replay',type(exc).__name__)
            last_send=time.monotonic()
            continue
        event=store.claim('inbox')
        if event:
            # Immediate acknowledgement while emulator/model work runs. This is
            # not a candidate notification and never makes a revision selectable.
            if event['body'].strip().casefold() not in ('status','help','cancel'):
                stopped.wait(max(0,3.1-(time.monotonic()-last_send)))
                try: transport('Command received. Reading or updating the selected Hinge conversation; this may take a few minutes.')
                except Exception: log.warning('Progress notification failed; no retry')
                last_send=time.monotonic()
            try:
                state,messages=controller.handle(event)
            except Exception as exc:
                log.error('Reply command stopped (%s)',type(exc).__name__)
                state={'mode':'idle'}
                # Locally raised runtime errors are actionable; SDK exceptions may
                # contain private HTTP data and are never sent to WhatsApp.
                detail=str(exc) if type(exc) in (RuntimeError,ValueError) else type(exc).__name__
                messages=[f'Command stopped: {detail}. No automatic resend. Send Begin after resolving the issue.']
            chunks=[]
            for body in messages:
                # Candidate sets remain a single message (3 x 500 + context), while
                # long lists/statuses may be split. Whole revision must dispatch.
                chunks.extend(body[i:i+1500] for i in range(0,len(body),1500))
            store.finish(event['sid'],state,chunks)
        else: stopped.wait(.3)
