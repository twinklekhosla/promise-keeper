"""Reads message exports into one shape: dicts with source, thread, sender, ts, text."""
from pathlib import Path

from . import email_export, whatsapp


def read_path(path: Path):
    """Yields messages from a WhatsApp .txt export, an .eml file, an .mbox file, or a folder of them."""
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file():
                yield from read_path(child)
        return
    suffix = path.suffix.lower()
    if suffix in (".txt", ".zip") and whatsapp.looks_like_export(path):
        yield from whatsapp.read(path)
    elif suffix == ".eml":
        yield from email_export.read_eml(path)
    elif suffix == ".mbox":
        yield from email_export.read_mbox(path)


def guess_me(path: Path) -> str | None:
    """Works out the user's own name from WhatsApp exports: in a one-to-one chat named after the other person,
    the other sender is the user. The name seen that way most often wins."""
    files = [p for p in (sorted(path.rglob("*")) if path.is_dir() else [path])
             if p.is_file() and p.suffix.lower() in (".txt", ".zip") and whatsapp.looks_like_export(p)]
    votes = {}
    for f in files:
        name, senders = whatsapp.participants(f)
        if name in senders and len(senders) == 2:
            me = (senders - {name}).pop()
            votes[me] = votes.get(me, 0) + 1
    return max(votes, key=votes.get) if votes else None


def guess_my_email(path: Path) -> str | None:
    """In someone's own mailbox, their address is on (almost) every message, as sender or recipient."""
    counts, total = {}, 0
    for m in read_path(path):
        if m["source"] != "email":
            continue
        total += 1
        for a in {m.get("sender_email", "")} | set(m.get("to_emails", [])):
            if a:
                counts[a] = counts.get(a, 0) + 1
    if total < 3 or not counts:
        return None
    best = max(counts, key=counts.get)
    return best if counts[best] >= 0.6 * total else None
