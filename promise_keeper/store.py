"""SQLite storage. Everything the app knows lives in one local file."""
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    source TEXT, thread TEXT, sender TEXT, is_me INTEGER,
    ts TEXT, text TEXT, scanned INTEGER DEFAULT 0, sender_email TEXT,
    sent INTEGER DEFAULT 0   -- 1 once this message (masked) has been sent to the model
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread, ts);
CREATE TABLE IF NOT EXISTS commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT, thread TEXT, source TEXT,
    owner TEXT,              -- 'me' = I promised, 'them' = someone promised me
    counterparty TEXT, what TEXT, due TEXT, quote TEXT,
    status TEXT DEFAULT 'open',   -- open | done | dropped | dismissed (not a promise) | expired (went quiet)
    status_note TEXT, status_message_id TEXT,
    amount REAL, currency TEXT,   -- money promises: "pay you back ₹1,200"
    weight TEXT,                  -- high | normal | low (household micro-stuff: hidden, never nags)
    snooze_until TEXT, created_ts TEXT, checked_until TEXT,
    UNIQUE(message_id, what)
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP, purpose TEXT, model TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, usd REAL
);
CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, response TEXT);
"""


# Columns added after the first release; older databases get them on open.
MIGRATIONS = [("messages", "sender_email", "TEXT"), ("messages", "sent", "INTEGER DEFAULT 0"),
              ("commitments", "amount", "REAL"), ("commitments", "currency", "TEXT"),
              ("commitments", "weight", "TEXT")]


def _migrate(conn):
    for table, column, kind in MIGRATIONS:
        if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


@contextmanager
def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def mark_sent(conn, rows):
    conn.executemany("UPDATE messages SET sent = 1 WHERE id = ?", [(r["id"],) for r in rows])


def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def spent_usd(conn) -> float:
    return conn.execute("SELECT COALESCE(SUM(usd), 0) FROM ledger").fetchone()[0]
