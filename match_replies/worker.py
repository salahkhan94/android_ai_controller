"""Process durable Telegram commands and deliver notifications without retries."""
import logging
import time

log=logging.getLogger(__name__)

def worker(store, controller, transport, stopped):
    store.recover()
    last_send=0
    while not stopped.is_set():
        # Deliver pending output before accepting choices against a revision.
        outbound=store.claim('outbox')
        if outbound:
            stopped.wait(max(0,1.1-(time.monotonic()-last_send)))
            if stopped.is_set(): return
            try:
                sid=transport(outbound['body'])
                store.notification(outbound['id'],'sent',sid)
            except Exception as exc:
                # HTTP timeout may already have sent; do not blindly replay.
                store.notification(outbound['id'],'uncertain')
                log.error('Telegram notification uncertain (%s); no automatic replay',type(exc).__name__)
            last_send=time.monotonic()
            continue
        event=store.claim('inbox')
        if event:
            # Immediate acknowledgement while emulator/model work runs. This is
            # not a candidate notification and never makes a revision selectable.
            if event['body'].strip().casefold() not in ('status','help','cancel'):
                stopped.wait(max(0,1.1-(time.monotonic()-last_send)))
                if stopped.is_set(): return
                try: transport('Command received. Reading or updating the selected Hinge conversation; this may take a few minutes.')
                except Exception: log.warning('Progress notification failed; no retry')
                last_send=time.monotonic()
            if stopped.is_set(): return
            try:
                state,messages=controller.handle(event)
            except Exception as exc:
                log.error('Reply command stopped (%s)',type(exc).__name__)
                state={'mode':'idle'}
                # Locally raised runtime errors are actionable; SDK exceptions may
                # contain private HTTP data and are never sent to Telegram.
                detail=str(exc) if type(exc) in (RuntimeError,ValueError) else type(exc).__name__
                messages=[f'Command stopped: {detail}. No automatic resend. Send Begin after resolving the issue.']
            chunks=[]
            for body in messages:
                # Split conservatively below Telegram limits, including emoji.
                # Every chunk must dispatch before a choice is accepted.
                chunks.extend(body[i:i+1500] for i in range(0,len(body),1500))
            store.finish(event['sid'],state,chunks)
        else: stopped.wait(.3)
