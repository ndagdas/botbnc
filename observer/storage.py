"""Sanitized review, watch and Telegram outbox jobs. PostgreSQL or local SQLite."""
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo


class Store:
    def __init__(self, url=None, path=None):
        self.url = url or os.getenv('OBSERVER_DATABASE_URL') or os.getenv('DATABASE_URL')
        self.path = path or os.getenv('OBSERVER_DB_PATH', '/tmp/signal-observer.sqlite3')
        self.backend = 'postgresql' if self.url else 'sqlite'
        self.restart_safe = bool(self.url) or not bool(os.getenv('DYNO'))
        with self.connection() as conn:
            self.execute(conn, '''CREATE TABLE IF NOT EXISTS observer_jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', due DOUBLE PRECISION NOT NULL,
                lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, created DOUBLE PRECISION NOT NULL,
                updated DOUBLE PRECISION NOT NULL, result TEXT)''')
            self.execute(conn, 'CREATE INDEX IF NOT EXISTS observer_jobs_due ON observer_jobs(state,due)')
            self.execute(conn, '''CREATE TABLE IF NOT EXISTS observer_budgets (
                name TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0)''')
            self.execute(conn, '''CREATE TABLE IF NOT EXISTS observer_candidates (
                id TEXT PRIMARY KEY, symbol TEXT NOT NULL, day TEXT NOT NULL,
                accepted DOUBLE PRECISION NOT NULL)''')
            self.execute(conn, 'CREATE INDEX IF NOT EXISTS observer_candidates_day ON observer_candidates(day)')

    @contextmanager
    def connection(self):
        if self.url:
            import psycopg
            from psycopg.rows import dict_row
            conn = psycopg.connect(self.url, connect_timeout=4, row_factory=dict_row)
        else:
            conn = sqlite3.connect(self.path, timeout=0.5)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA busy_timeout=500')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def execute(self, conn, sql, args=()):
        if self.url:
            sql = sql.replace('?', '%s')
        return conn.execute(sql, args)

    def enqueue(self, conn, key, kind, payload, due=None):
        now = time.time()
        cursor = self.execute(conn, '''INSERT INTO observer_jobs
            (id,kind,payload,due,created,updated) VALUES (?,?,?,?,?,?)
            ON CONFLICT(id) DO NOTHING''',
            (key, kind, json.dumps(payload, ensure_ascii=False, allow_nan=False),
             due if due is not None else now, now, now))
        return cursor.rowcount == 1

    def accept_review(self, key, data):
        with self.connection() as conn:
            if self.execute(conn, 'SELECT id FROM observer_jobs WHERE id=?', (key,)).fetchone():
                return False
            count = self.execute(conn, "SELECT COUNT(*) AS n FROM observer_jobs WHERE kind='review' AND state='pending'").fetchone()['n']
            if count >= int(os.getenv('QUEUE_MAX', '500')):
                raise OverflowError('Gözlem kuyruğu dolu')
            return self.enqueue(conn, key, 'review', data)

    def claim(self, now=None):
        now = time.time() if now is None else now
        with self.connection() as conn:
            if not self.url:
                conn.execute('BEGIN IMMEDIATE')
            suffix = ' FOR UPDATE SKIP LOCKED' if self.url else ''
            row = self.execute(conn, '''SELECT * FROM observer_jobs
                WHERE state='pending' AND due<=? AND lease_until<=?
                ORDER BY CASE kind WHEN 'notify' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, due
                LIMIT 1''' + suffix, (now, now)).fetchone()
            if not row:
                return None
            self.execute(conn, '''UPDATE observer_jobs SET lease_until=?, attempts=attempts+1,
                updated=? WHERE id=?''', (now + 120, now, row['id']))
            result = dict(row)
            result['payload'] = json.loads(result['payload'])
            result['attempts'] += 1
            return result

    def finish(self, conn, key, payload, *, state='done', due=None, result=None):
        now = time.time()
        self.execute(conn, '''UPDATE observer_jobs SET payload=?,state=?,due=?,lease_until=0,
            updated=?,result=? WHERE id=?''',
            (json.dumps(payload, ensure_ascii=False, allow_nan=False), state,
             now if due is None else due, now,
             json.dumps(result, ensure_ascii=False, allow_nan=False) if result is not None else None, key))

    def active_watches(self, conn):
        return [json.loads(r['payload']) for r in self.execute(conn,
            "SELECT payload FROM observer_jobs WHERE kind='watch' AND state='pending'").fetchall()]

    def consume_ai_budget(self, limit):
        key = 'ai:' + time.strftime('%Y-%m-%d', time.gmtime())
        with self.connection() as conn:
            self.execute(conn, 'INSERT INTO observer_budgets(name,used) VALUES (?,0) ON CONFLICT(name) DO NOTHING', (key,))
            return self.execute(conn, 'UPDATE observer_budgets SET used=used+1 WHERE name=? AND used<?', (key, limit)).rowcount == 1

    def candidate_day(self, now=None):
        return datetime.fromtimestamp(time.time() if now is None else now,
            ZoneInfo(os.getenv('CANDIDATE_TIMEZONE', 'Europe/Istanbul'))).strftime('%Y-%m-%d')

    def reserve_candidate(self, conn, key, symbol, now=None):
        """Atomic global Telegram AL-candidate cap; never counts exchange trades."""
        now = time.time() if now is None else now
        # A write before reads serializes candidate decisions in both backends.
        self.execute(conn, "INSERT INTO observer_budgets(name,used) VALUES ('candidate_mutex',0) ON CONFLICT(name) DO NOTHING")
        self.execute(conn, "UPDATE observer_budgets SET used=used WHERE name='candidate_mutex'")
        if self.execute(conn, 'SELECT id FROM observer_candidates WHERE id=?', (key,)).fetchone():
            return 'duplicate'
        day = self.candidate_day(now)
        count = self.execute(conn, 'SELECT COUNT(*) AS n FROM observer_candidates WHERE day=?', (day,)).fetchone()['n']
        if count >= max(0, int(os.getenv('MAX_AL_CANDIDATES_PER_DAY', '6'))):
            return 'day_limit'
        cooldown = max(0, int(os.getenv('AL_SYMBOL_COOLDOWN_MINUTES', '480'))) * 60
        if self.execute(conn, 'SELECT id FROM observer_candidates WHERE symbol=? AND accepted>? LIMIT 1', (symbol, now-cooldown)).fetchone():
            return 'symbol_cooldown'
        self.execute(conn, 'INSERT INTO observer_candidates(id,symbol,day,accepted) VALUES (?,?,?,?)', (key,symbol,day,now))
        return 'accepted'

    def candidates_today(self):
        with self.connection() as conn:
            return self.execute(conn, 'SELECT COUNT(*) AS n FROM observer_candidates WHERE day=?', (self.candidate_day(),)).fetchone()['n']

    def counts(self):
        with self.connection() as conn:
            return {r['kind'] + ':' + r['state']: r['n'] for r in self.execute(conn,
                'SELECT kind,state,COUNT(*) AS n FROM observer_jobs GROUP BY kind,state').fetchall()}

    def cleanup(self):
        with self.connection() as conn:
            self.execute(conn, "DELETE FROM observer_jobs WHERE state<>'pending' AND updated<?", (time.time() - 7 * 86400,))
            self.execute(conn, 'DELETE FROM observer_candidates WHERE accepted<?', (time.time() - 7 * 86400,))


_store = None
_lock = threading.Lock()


def get_store():
    global _store
    with _lock:
        if _store is None:
            _store = Store()
        return _store
