"""Parses WhatsApp "Export chat" files: Android .txt, iPhone .zip (with _chat.txt inside), day- or month-first dates."""
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

# Android: "18/09/26, 9:41 pm - Rohit Sharma: text"
# iPhone:  "[18/09/26, 9:41:05 PM] Rohit Sharma: text"
LINE = re.compile(
    r"^\[?(?P<date>\d{1,2}[/.]\d{1,2}[/.]\d{2,4}),?\s(?P<time>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?)\s?(?P<ampm>[ap]\.?\s?m\.?)?\]?"
    r"\s(?:-\s)?(?P<sender>[^:]+?):\s(?P<text>.*)$",
    re.IGNORECASE,
)
SKIP = ("<Media omitted>", "This message was deleted", "You deleted this message", "image omitted", "video omitted",
        "audio omitted", "sticker omitted", "document omitted", "GIF omitted", "Contact card omitted",
        "Missed voice call", "Missed video call", "null")
# iPhone exports put system events on normal-looking lines ("[..] Rohit: Messages and calls are end-to-end encrypted").
NAME = r"[A-Z][\w.'-]*(?: [A-Z][\w.'-]*)*"
SYSTEM = re.compile(
    r"end-to-end encrypted|created (this )?group|changed (the subject|this group's (icon|description))|"
    r"security code (with .+ )?changed|joined using this group's invite link|changed their phone number|"
    r"pinned a message|turned (on|off) disappearing messages|"
    rf"^(?:{NAME}|You) (?:added|removed) {NAME}(?:, {NAME})*(?: and {NAME})?$|^(?:{NAME}|You) left$")
EDITED = re.compile(r"\s*<This message was edited>\s*$")


def looks_like_export(path: Path) -> bool:
    head = [_clean(line) for line in _lines(path)[:8]]
    return any(LINE.match(line) for line in head)


def _lines(path: Path) -> list[str]:
    """Text lines of an export; for an iPhone .zip, of the _chat.txt inside it."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.endswith(".txt") and not n.startswith("__MACOSX")), None)
            if name is None:
                return []
            return io.TextIOWrapper(z.open(name), encoding="utf-8", errors="ignore").read().splitlines()
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read().splitlines()


def _day_first(lines) -> bool:
    """Indian/UK phones write day/month, US phones month/day. A part above 12 settles it; default day-first."""
    for line in lines:
        m = LINE.match(_clean(line))
        if m:
            a, b = (int(x) for x in re.split(r"[/.]", m["date"])[:2])
            if a > 12:
                return True
            if b > 12:
                return False
    return True


def _clean(line: str) -> str:
    # Newer exports use narrow no-break spaces and a left-to-right mark around times.
    return line.replace(" ", " ").replace("‎", "").replace("\xa0", " ").rstrip("\n")


def _parse_time(date_s, time_s, ampm, day_first=True):
    day, month, year = (int(x) for x in re.split(r"[/.]", date_s))
    if not day_first:
        day, month = month, day
    if year < 100:
        year += 2000
    parts = [int(x) for x in re.split(r"[:.]", time_s)]
    hour, minute = parts[0], parts[1]
    if ampm:
        pm = ampm.lower().startswith("p")
        hour = hour % 12 + (12 if pm else 0)
    return datetime(year, month, day, hour, minute)


def _thread_name(path: Path) -> str:
    name = path.stem
    if name == "_chat":  # an unzipped iPhone export: the folder carries the chat name
        name = path.parent.name
    name = name.removeprefix("WhatsApp Chat with ").removeprefix("WhatsApp Chat - ").strip()
    # Repeat downloads get "(2)" or " 2" added by the browser/Finder; they're the same chat.
    return re.sub(r"\s*(\(\d+\)|\s\d)$", "", name).strip("\u200e\u200f\u202a\u202c ")


def read(path: Path, day_first: bool | None = None):
    thread = _thread_name(path)
    lines = _lines(path)
    if day_first is None:
        day_first = _day_first(lines)
    current = None
    for raw in lines:
        line = _clean(raw)
        match = LINE.match(line)
        if match:
            if current:
                yield current
            text = EDITED.sub("", match["text"].strip())
            if SYSTEM.search(text):
                current = None
                continue
            current = {
                "source": "whatsapp",
                "thread": thread,
                "sender": match["sender"].strip(),
                "ts": _parse_time(match["date"], match["time"], match["ampm"], day_first).isoformat(),
                "text": text,
            }
        elif current and line.strip():
            current["text"] += "\n" + line.strip()  # continuation of a multi-line message
    if current:
        yield current


def participants(path: Path) -> tuple[str, set[str]]:
    """The chat name and everyone who sent a message in it."""
    return _thread_name(path), {m["sender"] for m in read(path)}


def is_skippable(text: str) -> bool:
    return not text.strip() or any(marker in text for marker in SKIP)
