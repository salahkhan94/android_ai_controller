"""Deterministic Telegram commands; only an explicit current choice can send."""
import hashlib
import json
import re
import uuid

HELP='Begin: list Your turn matches. Choose a name/number. Add context: revise replies. Reply with 1/2/3 to the candidate message, or use CODE 3. Cancel ends selection. Status shows state. Refresh profile: recapture the selected match. Add context instructions persist across runs.'

class Controller:
    def __init__(self, store, backend, generate, memory=None, profile_loader=None):
        self.store,self.backend,self.generate=store,backend,generate
        self.memory,self.profile_loader=memory,profile_loader
    def choices(self, state):
        history=dict(state['history'])
        if self.memory:
            record=self.memory.sync(state['match'],history)
            if state.get('memory_id') and record['id']!=state['memory_id']:
                raise RuntimeError('Persistent match identity changed.')
            state['memory_id']=record['id']
            if not record['profile']:
                record['profile']=self.profile_loader(state['match'])
                self.memory.save(record)
            record['context']=state['context']
            self.memory.save(record)
            history['profile_context']=record['profile']
        state['replies']=self.generate(history,state['context'])
        state['revision']=uuid.uuid4().hex[:10]
        state['mode']='reply'
        name=state['match']['name']
        recent=[m for m in state['history']['messages'] if m['sender'] in ('me','match')][-3:]
        transcript='\n\n'.join(f"{'You' if m['sender']=='me' else name}: {m['text']}" for m in recent)
        heading=f"Last {len(recent)} message{'s' if len(recent)!=1 else ''} (oldest first):"
        if not recent:
            heading='No conversation messages available.'
        body=f"{name}\nSelected preview: {state['match'].get('preview', '')}\n{heading}\n{transcript}\n\n"+'\n\n'.join(f'{i}. {r}' for i,r in enumerate(state['replies'],1))
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
            return s,[('Your turn:\n'+'\n'.join(f"{i}. {m['name']} — {m.get('preview', '')}" for i,m in enumerate(matches,1))+'\nWhich match would you like to reply to?') if matches else 'No matches in Your turn.']
        if s['mode']=='match':
            matches=s['matches']
            options=[m for m in matches if m['name'].casefold()==command]
            if text.isdecimal() and 1<=int(text)<=len(matches): options=[matches[int(text)-1]]
            if len(options)>1:
                return s,['More than one match has that name. Choose the list number:\n'+'\n'.join(f"{i}. {m['name']} — {m.get('preview','')}" for i,m in enumerate(matches,1) if m in options)]
            if len(options)!=1: return s,['Choose a unique name or the list number.']
            history=self.backend.history(options[0])
            context=[]
            if self.memory:
                record=self.memory.sync(options[0],history)
                context=record['context']
                s['memory_id']=record['id']
            s.update(match=options[0],history=history,context=context)
            return self.choices(s)
        if command=='refresh profile' and self.memory and s.get('match'):
            history=self.backend.history(s['match'])
            record=self.memory.sync(s['match'],history)
            refreshed=self.profile_loader(s['match'])
            if record['profile']: record.setdefault('previous_profiles',[]).append(record['profile'])
            record['profile']=refreshed
            self.memory.save(record)
            s['history']=history
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
            if self.memory: self.memory.sync(s['match'],current)
            if current['fingerprint']!=s['history']['fingerprint']:
                s['history']=current
                state,messages=self.choices(s)
                return state,['The conversation changed. Choose again from these updated replies.']+messages
            key=hashlib.sha256(json.dumps([s['match']['key'],current['fingerprint']]).encode()).hexdigest()
            reply=s['replies'][int(choice)-1]
            status=self.backend.send(s['match'],current,reply,lambda: self.store.claim_send(key,{'match':s['match'],'reply':reply}))
            self.store.send_result(key,status)
            if self.memory:
                record=self.memory.load(s['memory_id'])
                record['submissions'].append({'reply':reply,'status':status,'history_fingerprint':current['fingerprint']})
                self.memory.save(record)
            if status=='sent':
                return {'mode':'idle'},[f"Reply {choice} sent to {s['match']['name']}. Send Begin to refresh Your turn."]
            return {'mode':'idle'},['Send outcome is uncertain. I will not resend automatically. Check Hinge before continuing.']
        return s,['Send Begin to list Your turn matches.']
