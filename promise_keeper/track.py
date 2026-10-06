"""Checks later messages to see whether open promises were kept, dropped or moved."""
import re

from . import config, llm, store
from .extract import _line, _valid_date
from .privacy import Masker

SYSTEM = """You track whether promises were kept, using the messages that came after them.

For each promise decide:
- "done": the later messages clearly show it happened (sent, attached, booked, paid, "got it", "thanks for
  sending", or ME/they say it's done)
- "dropped": it was explicitly cancelled or is no longer needed
- "open": otherwise, including when someone only says they will still do it

Only messages sent after a promise was made can show it was kept. The message that made the promise
("Done, I'll handle it") is not evidence that it happened.

If the promise was clearly rescheduled ("sorry, will do it Monday"), keep it "open" and give new_due as
YYYY-MM-DD. Give evidence as the id of the message that decided it, and a note of at most 12 words.

Reply with JSON only: {"updates": [{"promise_id": "p1", "status": "...", "new_due": null,
"evidence": "m3", "note": "..."}]}"""


# Messages that can settle a promise: outcomes, excuses, reschedules. Others are only sent if they mention
# something that was promised ("Pics from last night" for "Send the pics").
OUTCOME = re.compile(
    r"\b(done|sent|paid|booked|got|received|mil gay\w*|mil gaya|aa gay\w*|aa gaya|ho gay\w*|kar di\w*|bhej di\w*|"
    r"here'?s|attached|returned|wapas|delivered|came|visited|completed|finished|fixed|theek|sorry|forgot|still|yet|"
    r"nahi|not|never|cancel\w*|no need|rehne do|tomorrow|tonight|kal|will|pakka|thanks|thank you|shukriya|"
    r"transferred|credited|refund\w*|pushed|shipped|merged|deployed|sorted|handled|called|spoke|talked|"
    r"ordered|finalised|finalized)\b", re.IGNORECASE)


def _stems(text):
    return {w[:4] for w in re.findall(r"[a-z]{4,}", text.lower())}


def _relevant(message, promises) -> bool:
    if OUTCOME.search(message["text"]):
        return True
    stems = _stems(message["text"])
    return any(stems & _stems(p["what"]) for p in promises)


def check_thread(conn, thread: str) -> int:
    promises = conn.execute(
        "SELECT * FROM commitments WHERE thread = ? AND status = 'open' ORDER BY created_ts", (thread,)
    ).fetchall()
    if not promises:
        return 0
    checked = min(p["checked_until"] for p in promises)
    all_later = conn.execute(
        "SELECT * FROM messages WHERE thread = ? AND ts > ? ORDER BY ts", (thread, checked)
    ).fetchall()
    later = [m for m in all_later if _relevant(m, promises)][:80]
    if not later:
        if all_later:
            conn.execute("UPDATE commitments SET checked_until = ? WHERE thread = ? AND status = 'open'",
                         (all_later[-1]["ts"], thread))
        return 0
    me = store.get_setting(conn, "me_names").split(",")[0].strip() or "ME"
    masker = Masker()
    pkeys = {f"p{i + 1}": p for i, p in enumerate(promises)}
    mkeys = {f"m{i + 1}": m for i, m in enumerate(later)}
    plist = "\n".join(
        f"[{k}] made {p['created_ts'][:16].replace('T', ' ')} by {'ME' if p['owner'] == 'me' else p['counterparty']}: "
        f"{masker.mask(p['what'])} (due {p['due'] or 'not set'})" for k, p in pkeys.items())
    mlist = "\n".join(_line(k, {**dict(m), "text": masker.mask(m["text"])}, masker=masker) for k, m in mkeys.items())
    user = f"ME = {me}\nConversation: {thread}\nPromises:\n{plist}\n\nLater messages:\n{mlist}"
    result = llm.chat_json(conn, SYSTEM, user, purpose="track", model=config.TRACK_MODEL)
    store.mark_sent(conn, later)

    def note_of(u):  # the prompt calls the user "ME"; the note is shown to them, so say "You"
        return re.sub(r"\bME\b", "You", masker.unmask(u.get("note", "")))

    changed = 0
    for u in result.get("updates", []):
        p = pkeys.get(str(u.get("promise_id")))
        if p is None:
            continue
        evidence = mkeys.get(str(u.get("evidence")))
        status = u.get("status")
        if status in ("done", "dropped") and (evidence is None or evidence["ts"] <= p["created_ts"]):
            continue  # only a message sent after the promise can close it
        new_due = _valid_date(u.get("new_due"))
        if status in ("done", "dropped"):
            conn.execute(
                "UPDATE commitments SET status = ?, status_note = ?, status_message_id = ? WHERE id = ?",
                (status, note_of(u), evidence["id"] if evidence else None, p["id"]))
            changed += 1
        elif new_due and new_due != p["due"]:
            conn.execute("UPDATE commitments SET due = ?, status_note = ? WHERE id = ?",
                         (new_due, note_of(u), p["id"]))
            changed += 1
    conn.execute("UPDATE commitments SET checked_until = ? WHERE thread = ? AND status = 'open'",
                 (all_later[-1]["ts"], thread))
    conn.commit()
    return changed


def track(conn) -> int:
    from concurrent.futures import ThreadPoolExecutor
    threads = [r["thread"] for r in conn.execute("SELECT DISTINCT thread FROM commitments WHERE status = 'open'")]
    conn.commit()

    def one(thread):
        with store.connect() as own:
            try:
                return check_thread(own, thread)
            except llm.BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001
                own.rollback()
                print(f"  couldn't check {thread!r} yet: {exc}")
                return 0

    with ThreadPoolExecutor(4) as pool:
        return sum(pool.map(one, threads))
