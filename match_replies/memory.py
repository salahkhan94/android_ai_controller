"""Durable per-match records, verified history continuity and profile snapshots."""
import json
import re
import uuid
from datetime import datetime, timezone


def is_relative_timestamp(text):
    return bool(re.fullmatch(r'(?:Today|Yesterday)(?:\s+(?:at\s+)?\d{1,2}:\d{2}\s*(?:AM|PM)?)?', text.strip(), re.I))


def notification_panel_texts(texts, name):
    """Recognize the complete observed notification opt-in panel, not arbitrary prose."""
    if not name: return set()
    labels={f'Get notifications from {name} only',
            'Timing is everything. This will not turn on notifications for other matches.',
            f'Enable for {name}'}
    return labels if labels.issubset(set(texts)) else set()


def core(messages, name=None):
    ignored=notification_panel_texts(
        [m['text'] for m in messages if m['sender']=='opening_context'], name)
    return [{'sender':m['sender'],'text':m['text']} for m in messages
            if m['sender'] in ('me','match','opening_context')
            and not (m['sender']=='opening_context' and (is_relative_timestamp(m['text']) or m['text'] in ignored))]


class Memory:
    def __init__(self, store):
        self.store=store
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS match_memory (id TEXT PRIMARY KEY, name TEXT, data TEXT)')

    def save(self, record):
        record['updated_at']=datetime.now(timezone.utc).isoformat()
        with self.store.db() as db:
            db.execute('INSERT OR REPLACE INTO match_memory VALUES (?,?,?)',
                       (record['id'],record['name'].casefold(),json.dumps(record,ensure_ascii=False)))

    def load(self, ident):
        with self.store.db() as db:
            row=db.execute('SELECT data FROM match_memory WHERE id=?',(ident,)).fetchone()
        if not row: raise RuntimeError('Match memory missing; send Begin.')
        return json.loads(row[0])

    def sync(self, match, history):
        if history.get('coverage')!='ui_boundary_verified':
            raise RuntimeError('Cannot attach memory to incomplete history.')
        current=core(history['messages'],match['name'])
        if not any(m['sender']=='opening_context' for m in current) or not any(m['sender']=='match' for m in current):
            raise RuntimeError('Insufficient conversation evidence for persistent identity.')
        with self.store.db() as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM match_memory WHERE name=?',(match['name'].casefold(),))]
        compatible=[r for r in rows if current[:len(core(r['history']['messages'],match['name']))]==core(r['history']['messages'],match['name'])]
        # A second same-name person needs distinct opening AND first incoming
        # evidence, plus an observed duplicate-name list. Never fork a known row
        # merely because its stored history no longer matches.
        def anchor(items):
            opening=[m['text'] for m in items if m['sender']=='opening_context']
            first=next((m['text'] for m in items if m['sender']=='match'),None)
            return opening,first
        opening,first=anchor(current)
        distinct=all((lambda old: old[0]!=opening and old[1]!=first)(anchor(core(r['history']['messages'],match['name']))) for r in rows)
        known_row=any(match['key'] in r.get('row_keys',[]) for r in rows)
        new_person=(not compatible and match.get('same_name_count',1)>1 and distinct and not known_row)
        if rows and len(compatible)!=1 and not new_person:
            raise RuntimeError('Stored match identity/history does not uniquely match this thread. Memory was not merged.')
        record=compatible[0] if compatible else {'id':uuid.uuid4().hex,'name':match['name'],'context':[], 'profile':None,'submissions':[]}
        keys=record.setdefault('row_keys',[])
        if match['key'] not in keys: keys.append(match['key'])
        record['history']=history
        self.save(record)
        return record
