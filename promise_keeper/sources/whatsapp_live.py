"""Live WhatsApp: reads new messages straight from WhatsApp for Mac's local database, no exports needed.

Only the chats the user picked are ever read. The database is opened read-only; WhatsApp keeps running.
"""
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .. import config

# WhatsApp (like all Core Data apps) stores times as seconds since 1 Jan 2001, UTC.
APPLE_EPOCH = 978307200


def available() -> bool:
    return config.WHATSAPP_DB.exists()


def _open():
    conn = sqlite3.connect(f"file:{quote(str(config.WHATSAPP_DB))}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def chats() -> list[str]:
    """Chat names, most recent first, for the picker. Only names are read here, never messages."""
    with closing(_open()) as conn:
        rows = conn.execute(
            "SELECT ZPARTNERNAME FROM ZWACHATSESSION WHERE ZPARTNERNAME IS NOT NULL AND ZPARTNERNAME != ''"
            " AND COALESCE(ZREMOVED, 0) = 0 ORDER BY ZLASTMESSAGEDATE DESC").fetchall()
    return list(dict.fromkeys(r[0] for r in rows))


def read(names: list[str], after: dict | None = None):
    """Text messages from the named chats only, in the same shape as the export readers, plus "row": the
    database row number. after = {chat: last row seen}; only newer rows are read (a delta, not a re-read)."""
    if not names:
        return
    after = after or {}
    marks = ",".join("?" * len(names))
    newer = " OR ".join("(s.ZPARTNERNAME = ? AND m.Z_PK > ?)" for _ in names)
    with closing(_open()) as conn:
        rows = conn.execute(
            f"""SELECT m.Z_PK AS row, s.ZPARTNERNAME AS chat, s.ZSESSIONTYPE AS kind, m.ZISFROMME AS from_me, m.ZMESSAGEDATE AS at,
                       m.ZTEXT AS text, m.ZPUSHNAME AS push, g.ZCONTACTNAME AS contact, g.ZFIRSTNAME AS first,
                       g.ZMEMBERJID AS jid
                FROM ZWAMESSAGE m
                JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
                LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER
                -- type 0 is text, 7 is text with a link preview; media, calls and system events (6) are skipped
                WHERE s.ZPARTNERNAME IN ({marks}) AND m.ZMESSAGETYPE IN (0, 7)
                  AND m.ZTEXT IS NOT NULL AND m.ZTEXT != '' AND ({newer})
                ORDER BY m.ZMESSAGEDATE""", [*names, *(x for n in names for x in (n, after.get(n, 0)))]).fetchall()
    for r in rows:
        if r["from_me"]:
            sender = "You"  # ingest treats "You" as the user, whatever their name
        elif r["kind"] == 0:  # one-to-one: the chat is named after the other person
            sender = r["chat"]
        else:
            sender = r["contact"] or r["first"] or _text(r["push"]) or (r["jid"] or "").split("@")[0] or "Someone"
        yield {
            "row": r["row"],
            "source": "whatsapp",
            "thread": r["chat"],
            "sender": sender,
            # Local wall-clock time, like the times in an exported chat.
            "ts": datetime.fromtimestamp(r["at"] + APPLE_EPOCH).replace(microsecond=0).isoformat(),
            "text": r["text"].strip(),
        }


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").strip()
    return (value or "").strip()
