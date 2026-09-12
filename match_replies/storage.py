"""Durable command inbox, single-user state, notifications and send claims."""
import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time

class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox (sid TEXT PRIMARY KEY, body TEXT, reply_to TEXT, revision TEXT, status TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, body TEXT, revision TEXT, status TEXT, sid TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, data TEXT, status TEXT);
            ''')
            columns={r[1] for r in db.execute('PRAGMA table_info(outbox)')}
            if 'delivery' not in columns: db.execute('ALTER TABLE outbox ADD COLUMN delivery TEXT')
        Path(path).chmod(0o600)
    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()
    def state(self):
        with self.db() as db:
            row=db.execute('SELECT data FROM state WHERE id=1').fetchone()
            return json.loads(row[0]) if row else {'mode':'idle'}
    def accept(self, sid, body, reply_to=''):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=db.execute('SELECT data FROM state WHERE id=1').fetchone()
            revision=json.loads(state[0]).get('revision','') if state else ''
            if db.execute('SELECT 1 FROM inbox WHERE sid=?',(sid,)).fetchone(): return False
            if body.strip().casefold() in ('cancel','begin'):
                db.execute('UPDATE inbox SET status="cancelled" WHERE status="queued"')
            return db.execute('INSERT OR IGNORE INTO inbox VALUES (?,?,?,?,?,?)',
                              (sid,body,reply_to,revision,'queued',time.time())).rowcount == 1
    def claim(self, table):
        if table not in ('inbox','outbox'): raise ValueError('Unknown queue')
        key='sid' if table=='inbox' else 'id'
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute(f'SELECT * FROM {table} WHERE status="queued" ORDER BY created LIMIT 1').fetchone()
            if row: db.execute(f'UPDATE {table} SET status="working" WHERE {key}=?',(row[key],))
            return dict(row) if row else None
    def finish(self, sid, state, messages):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR REPLACE INTO state VALUES (1,?)',(json.dumps(state),))
            for body in messages:
                db.execute('INSERT INTO outbox(body,revision,status,created) VALUES (?,?,?,?)',
                           (body,state.get('revision',''),'queued',time.time()))
            db.execute('UPDATE inbox SET status="done" WHERE sid=?',(sid,))
    def notification(self, ident, status, sid=None):
        with self.db() as db:
            db.execute('UPDATE outbox SET status=?,sid=? WHERE id=?',(status,sid,ident))
    def delivery(self, sid, status):
        with self.db() as db:
            db.execute('UPDATE outbox SET delivery=? WHERE sid=?',(status,sid))
    def displayed(self, revision, reply_to=''):
        with self.db() as db:
            rows=db.execute('SELECT sid,status,delivery FROM outbox WHERE revision=?',(revision,)).fetchall()
            return bool(rows) and all(r['status']=='sent' and r['delivery'] not in ('failed','undelivered') for r in rows) and (not reply_to or any(r['sid']==reply_to for r in rows))
    def claim_send(self, key, data):
        with self.db() as db:
            return db.execute('INSERT OR IGNORE INTO attempts VALUES (?,?,?)',
                              (key,json.dumps(data),'attempted_unknown')).rowcount == 1
    def send_result(self, key, status):
        with self.db() as db: db.execute('UPDATE attempts SET status=? WHERE id=?',(status,key))
    def recover(self):
        # A crash during processing cannot safely replay a choice or HTTP send.
        with self.db() as db:
            n=db.execute('UPDATE inbox SET status="interrupted" WHERE status="working"').rowcount
            db.execute('UPDATE outbox SET status="uncertain" WHERE status="working"')
            if n:
                db.execute('INSERT OR REPLACE INTO state VALUES (1,?)',(json.dumps({'mode':'idle'}),))
                db.execute('UPDATE inbox SET status="interrupted" WHERE status="queued"')
    def setting(self, key, default=None):
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
            row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else default
    def set_setting(self, key, value):
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value)))
    def reset_session(self):
        # Preserve attempt ledger and inbox deduplication across transport migration.
        with self.db() as db:
            db.execute('UPDATE inbox SET status="interrupted" WHERE status IN ("queued","working")')
            db.execute('UPDATE outbox SET status="uncertain" WHERE status IN ("queued","working")')
            db.execute('INSERT OR REPLACE INTO state VALUES (1,?)',(json.dumps({'mode':'idle'}),))
