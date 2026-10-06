"""Daily digest, follow-up drafts, and notifications (macOS and optional Telegram)."""
import subprocess
import urllib.parse
import urllib.request
from datetime import date, timedelta

from . import config, llm, store
from .extract import _line
from .privacy import Masker

SOON_DAYS = 3
STALE_DAYS = 7


def _open(conn, today: date):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM commitments WHERE status = 'open' AND COALESCE(weight, 'normal') != 'low'"
        " AND (snooze_until IS NULL OR snooze_until <= ?)"
        " ORDER BY COALESCE(due, '9999'), created_ts", (today.isoformat(),))]


def digest(conn, today: date | None = None) -> dict:
    today = today or config.today()
    soon = (today + timedelta(days=SOON_DAYS)).isoformat()
    stale = (today - timedelta(days=STALE_DAYS)).isoformat()
    t = today.isoformat()
    groups = {"i_owe_overdue": [], "i_owe_soon": [], "owed_to_me_overdue": [], "no_date_stale": []}
    for c in _open(conn, today):
        if c["due"] and c["due"] < t:
            groups["i_owe_overdue" if c["owner"] == "me" else "owed_to_me_overdue"].append(c)
        elif c["due"] and c["owner"] == "me" and c["due"] <= soon:
            groups["i_owe_soon"].append(c)
        elif not c["due"] and c["created_ts"][:10] <= stale:
            groups["no_date_stale"].append(c)
    return groups


def money(conn) -> dict:
    """Open money promises, both ways: {"owed_to_me": {"INR": 1699.0}, "i_owe": {...}}."""
    out = {"owed_to_me": {}, "i_owe": {}}
    for r in conn.execute("SELECT owner, currency, SUM(amount) total FROM commitments WHERE status = 'open'"
                          " AND amount IS NOT NULL GROUP BY owner, currency"):
        out["owed_to_me" if r["owner"] == "them" else "i_owe"][r["currency"] or "INR"] = r["total"]
    return out


def digest_text(groups: dict) -> str:
    def days_late(c):
        return (config.today() - date.fromisoformat(c["due"])).days

    lines = []
    if groups["i_owe_overdue"]:
        lines.append("You promised, and it's late:")
        lines += [f"  • {c['what']} for {c['counterparty']} ({days_late(c)}d late)" for c in groups["i_owe_overdue"]]
    if groups["i_owe_soon"]:
        lines.append("Coming up:")
        lines += [f"  • {c['what']} for {c['counterparty']} (due {c['due']})" for c in groups["i_owe_soon"]]
    if groups["owed_to_me_overdue"]:
        lines.append("Others promised you, and it's late:")
        lines += [f"  • {c['counterparty']}: {c['what']} ({days_late(c)}d late)" for c in groups["owed_to_me_overdue"]]
    if groups["no_date_stale"]:
        lines.append("No date, still open after a week:")
        lines += [f"  • {c['what']} ({'you' if c['owner'] == 'me' else c['counterparty']})" for c in groups["no_date_stale"]]
    return "\n".join(lines) or "Nothing due. All promises on track."


DRAFT_SYSTEM = """You write one short message for ME to send in an existing conversation.
Match the language and tone of the conversation (Hinglish if they write Hinglish, formal for work email).
Sound like a real person, not a bot. At most 60 words. No subject line, no placeholders like [Name].
Never invent facts, reasons or plans that aren't in the conversation (no "I need it for a project").

Cases:
- ME promised and it's late: own it briefly and give a concrete new time within 2 days.
- ME promised and it's coming up: a short heads-up that it's on track.
- Someone promised ME and it's late: a friendly, specific reminder that makes it easy for them to reply.
- No date and it's been a while: a light check-in on whether it's still happening.

Reply with JSON only: {"message": "..."}"""


def draft(conn, commitment_id: int, today: date | None = None) -> str:
    today = today or config.today()
    c = conn.execute("SELECT * FROM commitments WHERE id = ?", (commitment_id,)).fetchone()
    if c is None:
        raise KeyError(commitment_id)
    recent = conn.execute(
        "SELECT * FROM messages WHERE thread = ? ORDER BY ts DESC LIMIT 8", (c["thread"],)).fetchall()[::-1]
    masker = Masker()
    convo = "\n".join(_line(f"m{i + 1}", {**dict(m), "text": masker.mask(m["text"])}, masker=masker) for i, m in enumerate(recent))
    me = store.get_setting(conn, "me_names").split(",")[0].strip() or "ME"
    late = bool(c["due"]) and c["due"] < today.isoformat()
    if c["owner"] == "me":
        case = ("ME promised and it's late" if late else
                "ME promised and it's coming up" if c["due"] else "No date and it's been a while")
        promise = f"ME ({me}) promised {c['counterparty']}: {masker.mask(c['what'])}"
    else:
        case = "Someone promised ME and it's late" if late else "No date and it's been a while"
        promise = (f"{c['counterparty']} promised ME ({me}): {masker.mask(c['what'])}. "
                   f"ME is waiting on {c['counterparty']}; do not write as if ME owes it")
    user = (f"Today is {today:%Y-%m-%d %a}.\nYou are writing as ME ({me}), to {c['counterparty']}.\n"
            f"Case: {case}.\nPromise: {promise} (due {c['due'] or 'not set'}; "
            f"said: \"{masker.mask(c['quote'])}\")\n\nRecent conversation ({c['thread']}):\n{convo}")
    result = llm.chat_json(conn, DRAFT_SYSTEM, user, purpose="draft", max_tokens=2000)
    store.mark_sent(conn, recent)
    return masker.unmask(result.get("message", "")).strip()


def _applescript(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(text: str, title: str = "Promise Keeper") -> list[str]:
    """Sends the digest as a macOS notification, and to Telegram when a bot token is configured."""
    sent = []
    first_line = text.splitlines()[0] if text else ""
    script = f"display notification {_applescript(text[:230])} with title {_applescript(title)} subtitle {_applescript(first_line)}"
    if subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0:
        sent.append("macos")
    if config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID:
        data = urllib.parse.urlencode({"chat_id": config.TELEGRAM_CHAT_ID, "text": f"{title}\n\n{text}"}).encode()
        url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
        with urllib.request.urlopen(url, data=data, timeout=30) as r:
            if r.status == 200:
                sent.append("telegram")
    return sent
