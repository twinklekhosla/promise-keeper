"""Loads message exports into the local database."""
import hashlib
from pathlib import Path

import json

from . import sources, store
from .sources import whatsapp_live
from .sources.whatsapp import is_skippable


def _is_me(conn, message) -> bool:
    names = {n.strip().lower() for n in store.get_setting(conn, "me_names").split(",") if n.strip()}
    emails = {e.strip().lower() for e in store.get_setting(conn, "me_emails").split(",") if e.strip()}
    sender = message["sender"].strip().lower()
    # Some exports label the user's own messages "You" instead of their name.
    return sender in names or sender in ("you", "me") or message.get("sender_email", "") in emails


def ensure_me(conn, path: Path) -> str | None:
    """If the user hasn't said who they are, work it out: their name from one-to-one WhatsApp chats, their
    address from their own mailbox."""
    if not store.get_setting(conn, "me_emails"):
        address = sources.guess_my_email(path)
        if address:
            store.set_setting(conn, "me_emails", address)
    if store.get_setting(conn, "me_names"):
        return None
    me = sources.guess_me(path)
    if me:
        store.set_setting(conn, "me_names", me)
    return me


def _changed_files(conn, path: Path):
    """Files under path that are new or modified since the last import (big iPhone zips aren't re-read)."""
    files = [p for p in (sorted(path.rglob("*")) if path.is_dir() else [path]) if p.is_file()]
    for f in files:
        key, stamp = f"file:{f.resolve()}", f"{f.stat().st_mtime_ns}:{f.stat().st_size}"
        if store.get_setting(conn, key) != stamp:
            yield f, key, stamp


def ingest(conn, path: Path, only_changed: bool = False) -> int:
    if only_changed:
        added = 0
        for f, key, stamp in list(_changed_files(conn, path)):
            added += ingest(conn, f)
            store.set_setting(conn, key, stamp)
        return added
    return _insert(conn, sources.read_path(path))


def live_chats(conn) -> list[str]:
    """The WhatsApp chats the user picked for live sync. Nothing else is read."""
    return json.loads(store.get_setting(conn, "whatsapp_chats") or "[]")


def sync_whatsapp(conn) -> int:
    """Adds new messages from the picked chats in WhatsApp for Mac. Free: no model calls here."""
    names = live_chats(conn)
    if not names or not whatsapp_live.available():
        return 0
    # Only rows newer than the last one seen in each chat are read.
    seen = json.loads(store.get_setting(conn, "whatsapp_seen") or "{}")
    messages = list(whatsapp_live.read(names, after=seen))
    for m in messages:
        seen[m["thread"]] = max(seen.get(m["thread"], 0), m["row"])
    added = _insert(conn, messages)
    store.set_setting(conn, "whatsapp_seen", json.dumps(seen, ensure_ascii=False))
    return added


def _insert(conn, messages) -> int:
    added = 0
    for m in messages:
        if is_skippable(m["text"]):
            continue
        key = "|".join([m["source"], m["thread"], m["ts"], m["sender"], m["text"]])
        mid = hashlib.sha1(key.encode()).hexdigest()[:16]
        is_me = int(_is_me(conn, m))
        # The same message can arrive from an export (times to the minute, you by name) and from live sync
        # (seconds, you as "You"): same chat, side, minute and text means it's already here.
        minute = m["ts"][:16]
        if conn.execute("SELECT 1 FROM messages WHERE thread = ? AND ts BETWEEN ? AND ? AND is_me = ? AND text = ? AND id != ?",
                        (m["thread"], minute, minute + ":59", is_me, m["text"], mid)).fetchone():
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO messages (id, source, thread, sender, is_me, ts, text, sender_email)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, m["source"], m["thread"], m["sender"], is_me, m["ts"], m["text"], m.get("sender_email")),
        )
        added += cur.rowcount
    return added
