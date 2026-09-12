"""Deterministic Telegram commands; only an explicit current choice can send."""
import hashlib
import json
import re
import uuid

HELP='Begin: list Your turn matches. Choose a name/number. Add context: revise replies. Reply with 1/2/3 to the candidate message, or use CODE 3. Cancel ends selection. Status shows state.'

class Controller:
    def __init__(self, store, backend, generate):
        self.store,self.backend,self.generate=store,backend,generate
    def choices(self, state):
        state['replies']=self.generate(state['history'],state['context'])
        state['revision']=uuid.uuid4().hex[:10]
        state['mode']='reply'
        name=state['match']['name']
        latest=next((m['text'] for m in reversed(state['history']['messages']) if m['sender']=='match'),'')
        body=f"{name}\nLatest incoming: {latest}\n\n"+'\n\n'.join(f'{i}. {r}' for i,r in enumerate(state['replies'],1))
        body+=f"\n\nReply to THIS message with 1, 2, or 3; or send {state['revision']} 3 (replace 3 with your choice). Add context to revise."
        return state,[body]
    def handle(self, event):
        s=self.store.state(); text=event['body'].strip(); command=text.casefold()
        if command=='help': return s,[HELP]
        if command=='status': return s,[f"State: {s['mode']}. {s.get('match',{}).get('name','')}"]
        if command=='cancel': return {'mode':'idle'},['Selection cancelled. Send Begin to restart.']
        if command=='begin':
            matches=self.backend.list_matches()
            s={'mode':'match','matches':matches}
            return s,[('Your turn:\n'+'\n'.join(f"{i}. {m['name']}" for i,m in enumerate(matches,1))+'\nWhich match would you like to reply to?') if matches else 'No matches in Your turn.']
        if s['mode']=='match':
            matches=s['matches']
            options=[m for m in matches if m['name'].casefold()==command]
            if text.isdecimal() and 1<=int(text)<=len(matches): options=[matches[int(text)-1]]
            if len(options)!=1: return s,['Choose a unique name or the list number.']
            history=self.backend.history(options[0])
            s.update(match=options[0],history=history,context=[])
            return self.choices(s)
        if s['mode']=='context':
            if len(text)>4000: return s,['Please limit extra context to 4000 characters.']
            s['context'].append(text.removeprefix('Context:').strip())
            s['history']=self.backend.history(s['match'])
            return self.choices(s)
        if s['mode']=='reply':
            if command=='add context':
                s['mode']='context';s.pop('revision',None)
                return s,['What is the context?']
            parts=text.split()
            code=parts[0] if len(parts)==2 else ''
            choice=parts[-1] if parts else ''
            if choice not in ('1','2','3'): return s,[HELP]
            # Bare digits need reply-to binding, including the first set. A delayed
            # unthreaded number must never select a newly generated candidate.
            if (code and code!=s['revision']) or (not code and not event.get('reply_to')):
                return s,[f"Reply directly to the current candidate message, or send {s['revision']} {choice}."]
            if event.get('revision')!=s['revision'] or not self.store.displayed(s['revision'],event.get('reply_to','')):
                return s,['That choice refers to an old or undispatched candidate set. Use the current message.']
            current=self.backend.history(s['match'])
            if current['fingerprint']!=s['history']['fingerprint']:
                s['history']=current
                state,messages=self.choices(s)
                return state,['The conversation changed. Choose again from these updated replies.']+messages
            key=hashlib.sha256(json.dumps([s['match']['key'],current['fingerprint']]).encode()).hexdigest()
            reply=s['replies'][int(choice)-1]
            status=self.backend.send(s['match'],current,reply,lambda: self.store.claim_send(key,{'match':s['match'],'reply':reply}))
            self.store.send_result(key,status)
            if status=='sent':
                return {'mode':'idle'},[f"Reply {choice} sent to {s['match']['name']}. Send Begin to refresh Your turn."]
            return {'mode':'idle'},['Send outcome is uncertain. I will not resend automatically. Check Hinge before continuing.']
        return s,['Send Begin to list Your turn matches.']
