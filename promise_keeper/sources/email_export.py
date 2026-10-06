"""Reads emails from .eml files or an .mbox export (for example Gmail via Google Takeout)."""
import email
import mailbox
import re
from email import policy
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

QUOTE_START = re.compile(r"^(On .+wrote:|-----Original Message-----|From: .+)$", re.MULTILINE)


def _thread(subject: str) -> str:
    subject = re.sub(r"^\s*((re|fwd?|fw)\s*:\s*)+", "", subject or "(no subject)", flags=re.IGNORECASE)
    return "Email: " + subject.strip()


def _body(msg) -> str:
    part = msg.get_body(preferencelist=("plain",)) if hasattr(msg, "get_body") else None
    text = part.get_content() if part else ""
    if not text and not msg.is_multipart():
        text = msg.get_payload(decode=True).decode(errors="ignore")
    # Keep only the new text: drop the quoted earlier conversation.
    match = QUOTE_START.search(text)
    if match:
        text = text[: match.start()]
    lines = [line for line in text.splitlines() if not line.startswith(">")]
    return "\n".join(lines).strip()


BULK_HEADERS = ("List-Unsubscribe", "List-Id")


def _is_bulk(msg) -> bool:
    """Newsletters and marketing carry list headers or say so in Precedence; they're never personal promises."""
    return any(msg.get(h) for h in BULK_HEADERS) or (msg.get("Precedence", "").lower() in ("bulk", "list", "junk"))


def _to_message(msg):
    if _is_bulk(msg):
        return None
    sender_name, sender_addr = (getaddresses([msg.get("From", "")]) or [("", "")])[0]
    try:
        sent = parsedate_to_datetime(msg.get("Date"))
        # In the user's own time zone, so "by EOD" from abroad lands on the right day.
        ts = (sent.astimezone() if sent.tzinfo else sent).replace(tzinfo=None).isoformat()
    except (TypeError, ValueError):
        return None
    to = [a.lower() for _, a in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", [])) if a]
    return {
        "source": "email",
        "thread": _thread(msg.get("Subject", "")),
        "sender": sender_name or sender_addr,
        "sender_email": sender_addr.lower(),
        "to_emails": to,
        "ts": ts,
        "text": _body(msg),
    }


def read_eml(path: Path):
    with open(path, "rb") as f:
        message = _to_message(email.message_from_binary_file(f, policy=policy.default))
    if message:
        yield message


def read_mbox(path: Path):
    for raw in mailbox.mbox(path):
        message = _to_message(email.message_from_bytes(raw.as_bytes(), policy=policy.default))
        if message:
            yield message
