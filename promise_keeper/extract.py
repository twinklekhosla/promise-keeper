"""Finds promises in new messages with Nemotron.

Only message windows that contain a promise-like phrase are sent to the model, and contact
details are masked first, so most of the inbox never leaves the machine.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from . import config, llm, store
from .privacy import Masker

CUES = re.compile(
    r"\b(i'?ll|i will|we'?ll|we will|will|won't|shall|promise|let me|gonna|going to|"
    r"tomorrow|tonight|today|next (week|weekend|month)|this (week|weekend)|eod|asap|within|"
    r"by (mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"dunga|dungi|dega|degi|denge|karunga|karungi|karega|karegi|karenge|karta|karti|bhej\w*|pakka|kal|parso|"
    r"aaunga|aaungi|aa jaunga|aa jayega|lunga|lungi|ho jayega|kar dena)\b"
    # Hindi in Devanagari: will give / do / send / come / take, tomorrow, surely, promise
    r"|दूंगा|दूँगा|दूंगी|करूंगा|करूँगा|करूंगी|भेज|कल|पक्का|वादा|आऊंगा|आऊँगा|लूंगा|लूँगा|हो जाएगा",
    re.IGNORECASE,
)
WINDOW = 40
# Requests: the reply to one ("sure", "haan") is often the promise, so they pull in their context too.
REQUESTS = re.compile(
    r"\b(can you|could you|would you|will you|please|pls|plz|kar dena|kar do|bhej dena|bhej do|de dena|"
    r"de do|la dena|remind)\b", re.IGNORECASE)
# Someone committing themselves ("I'll", "main ... dunga"), as opposed to asking ME to do something.
FIRST_PERSON = re.compile(
    r"\b(i'?ll|i will|i'?m going to|i shall|we'?ll|we will|main|mai|hum|dunga|dungi|karunga|karungi|lunga|lungi|"
    r"aaunga|aaungi|bhejunga|bhejungi|rahunga|rahungi|bhejti|bhejta|leke)\b|मैं|दूंगा|दूँगा|करूंगा|करूँगा|भेजूंगा",
    re.IGNORECASE)

SYSTEM = """You find promises in one person's chats and emails. That person is called ME.

A promise is someone committing to do something concrete for someone else, for example
"I'll send the deck tomorrow", "kal tak bhej dunga", or "Sure, will do tonight" in reply to a request.

Report only:
- promises ME made to someone (owner "me"), and
- promises someone else made to ME, or to a group ME is part of (owner "them").

Everyday chats are mostly not promises. Ignore:
- vague or tentative replies: "let's catch up sometime", "will try to come", "try karta hoon", "dekhta hoon", "maybe"
- urging or asking others: "next time pakka aana", "you must come", "do send the pics"
- joining a plan or RSVPs: "I'm in", "count me in", "see you there"
- saying yes to lending or permission ("sure, take it", "go ahead"); the borrower's "will return it" IS a promise
- things due within the next hour or so: "on my way", "there in 20", "calling you in 5"
- promises to oneself ("gym from tomorrow"), jokes and exaggeration ("I'll kill you 😂"), predictions
  ("Kohli will score a century"), forwards and chain messages, marketing and announcements to many people
- things finished in the same message, and promises between other people that don't involve ME (in a group
  chat, "I'll pay Karan" is between others, skip it)

If a later message only repeats or reschedules the same promise that hasn't happened yet ("sorry, will do it
tonight"), report the promise once, with message_id of the message where it was FIRST made, and the latest
due date. A new promise that follows up on one that already happened is a separate promise (e.g. "will deliver
today", then "delivered", then "sorry it's damaged, we'll send a replacement Monday" are two promises).

Go through every message one by one. One message can hold several promises; list each separately
(e.g. "Done, I'll handle the car. Will send the itinerary by Sunday" is two promises). A reply that accepts
a request ("sure, will do", "haan kar dunga") is a promise made in that reply.

For each promise give:
- message_id: the id of the message where it was made
- owner: "me" or "them"
- counterparty: the other person (for "me", who it was promised to; for "them", who promised)
- promised_to: who the promise was made to: "ME", "group" (everyone in a group chat), or the person's name
- what: a short action phrase from ME's point of view, naming the thing, e.g. "Send the pitch deck",
  "Pay me back ₹1,200", "Check Coldplay tribute prices"
- due: YYYY-MM-DD, worked out from that message's date ("tomorrow" = next day, "by Friday" = the coming
  Friday, a weekday name always means its next occurrence after the message date (so "by Monday" said on a
  Monday is the following Monday), "this weekend" = the coming Saturday, "EOD"/"tonight" = same day,
  "next week" = Friday of next week, "within 2 working days" = count weekdays, and for a range like "5-7 working
  days" use the later end). If the date comes from
  context (e.g. "this Saturday" earlier in the chat), use it; an explicit date in the chat ("2 October") wins
  over a weekday name. If no time was given, due is null; never just copy the message's own date.

Messages are an excerpt: only promise-like lines and their neighbours are included.
- quote: the exact words from the message, at most 20 words
- weight: how much it matters if this is forgotten
  "high": money, documents, bookings and tickets, official or work things, deadlines, anything done for a third
  person or a business
  "normal": a real favour or errand the other person would notice if it didn't happen
  "low": tiny household or couple logistics that happen on their own the same day ("make kebabs", "come today",
  "use that one", "delete it", "drop Nono back", breakfast, small purchases at home); arrival times and meet-up
  plans between people who see each other daily ("come around 7:30", "check if Friday works"); vague "later"
  intentions ("buy Asics later"); conditional notices ("I'll tell you if it exceeds 20k").
  Never "low": sending or sharing something (a password, photos, a link, a document), returning something
  borrowed, paying, booking, introducing people, anything for a third person
- amount: only if someone commits to PAY, TRANSFER, RETURN or REFUND a specific sum (e.g. "I'll pay you back
  ₹1,200" -> 1200). Not prices, budgets, limits or conditions ("if it exceeds 20k", "it costs 5k"); else null
- currency: "INR", "USD", etc. for amount (₹ and "rs" mean INR); else null
- confidence: 0 to 1

Text may be English, Hindi or Hinglish. Contact details appear as placeholders like <PHONE_1>; keep them.
Reply with JSON only: {"commitments": [...]}"""


def _line(key, m, me_label="ME", masker=None):
    """One message as the model sees it. Non-contacts show up as phone numbers, so the sender is masked too."""
    ts = datetime.fromisoformat(m["ts"])
    who = me_label if m["is_me"] else m["sender"]
    if masker is not None:
        who = masker.mask(who)
    return f"[{key}] {ts:%Y-%m-%d %a %H:%M} | {who} | {m['text']}"


def scan_thread(conn, thread: str, since: date) -> int:
    me_names = [n.strip() for n in store.get_setting(conn, "me_names").split(",") if n.strip()]
    rows = conn.execute(
        "SELECT * FROM messages WHERE thread = ? AND ts >= ? ORDER BY ts", (thread, since.isoformat())
    ).fetchall()
    found = 0
    for start in range(0, len(rows), WINDOW):
        window = rows[start:start + WINDOW]
        new = [r for r in window if not r["scanned"]]
        if not new:
            continue
        selected = _with_context(window, new)
        if selected:
            found += _extract_window(conn, thread, selected, me_names)
        conn.executemany("UPDATE messages SET scanned = 1 WHERE id = ?", [(r["id"],) for r in new])
        conn.commit()
    return found


MONTHS = r"jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sep(tember)?|oct(ober)?|nov(ember)?|dec(ember)?"
DATE_TALK = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|kal|tonight|weekend|"
                       rf"\d{{1,2}}(st|nd|rd|th)|\d{{1,2}} ({MONTHS}))\b", re.IGNORECASE)
SAME_DAY = re.compile(r"\b(today|tonight|eod|aaj|abhi|now|this (evening|afternoon|morning)|shaam|raat|in an? hour)\b",
                      re.IGNORECASE)


def _with_context(window, new):
    """Only promise-like messages and requests leave the machine, with just enough context: the message a
    promise replies to (if someone else sent it), and the two replies after a request."""
    new_ids = {r["id"] for r in new}
    keep = set()
    for i, r in enumerate(window):
        if r["id"] not in new_ids:
            continue
        if CUES.search(r["text"]):
            keep.add(i)
            if i > 0 and window[i - 1]["sender"] != r["sender"]:
                keep.add(i - 1)
            if not DATE_TALK.search(r["text"]):  # "I'll book a table" gets its day from "Toit on Saturday?"
                keep.update(next(([j] for j in range(i - 1, max(-1, i - 6), -1) if DATE_TALK.search(window[j]["text"])), []))
        if REQUESTS.search(r["text"]):
            keep.update(j for j in (i, i + 1, i + 2) if j < len(window))
    return [window[i] for i in sorted(keep)]


def _extract_window(conn, thread, window, me_names) -> int:
    """Sends one window to the model; if the answer gets cut off, splits the window in half and retries."""
    me = me_names[0] if me_names else "ME"
    masker = Masker()
    keys = {f"m{i + 1}": r for i, r in enumerate(window)}
    listing = "\n".join(_line(k, {**dict(r), "text": masker.mask(r["text"])}, masker=masker) for k, r in keys.items())
    user = f"ME = {me}\nConversation: {thread} ({window[0]['source']})\n{_not_promises(conn, masker)}Messages:\n{listing}"
    try:
        result = llm.chat_json(conn, SYSTEM, user, purpose="extract")
    except llm.Truncated:
        if len(window) < 6:
            raise
        half = len(window) // 2
        return (_extract_window(conn, thread, window[:half], me_names)
                + _extract_window(conn, thread, window[half:], me_names))
    store.mark_sent(conn, window)
    found = 0
    # Oldest first, so a later "sorry, Monday pakka" can be recognised as moving an earlier promise.
    items = sorted(result.get("commitments", []),
                   key=lambda c: keys[str(c.get("message_id"))]["ts"] if str(c.get("message_id")) in keys else "")
    for c in items:
        row = keys.get(str(c.get("message_id")))
        if row is None or row["scanned"] or float(c.get("confidence", 0)) < 0.6:
            continue
        row = _promise_message(window, row, c.get("owner"))
        if row is not None:
            found += _save(conn, thread, row, c, masker, me_names)
    return found


def _not_promises(conn, masker) -> str:
    """The user's own "not a promise" clicks, shown to the model so it skips things like them."""
    quotes = [r["q"] for r in conn.execute(
        "SELECT COALESCE(NULLIF(c.quote, ''), m.text) AS q FROM commitments c LEFT JOIN messages m ON m.id = c.message_id"
        " WHERE c.status = 'dismissed' ORDER BY c.id DESC LIMIT 8") if r["q"]]
    if not quotes:
        return ""
    return "ME said these earlier lines were NOT promises worth tracking; skip similar ones:\n" + \
        "\n".join(f'- "{masker.mask(q)}"' for q in quotes) + "\n\n"


def _promise_message(window, row, claimed_owner):
    """The model sometimes points at the request instead of the reply that accepted it. If it says ME promised
    but the message isn't ME's, use ME's next message in the window; if there is none, drop it."""
    if claimed_owner != "me" or row["is_me"]:
        return None if claimed_owner == "them" and row["is_me"] else row
    later = [r for r in window if r["ts"] > row["ts"]]
    return next((r for r in later[:3] if r["is_me"]), None)


# Words that say nothing about *what* was promised, in English and Hinglish.
STOP = set("""
a an the to for of on in at by with from and or but so if as it its this that these those there here
i me my we us our you your he him his she her they them their it's i'll i'm we'll you'll ll s re ve d t
is are was were be been am will would shall should can could may might must do does did done have has had
get got go going come coming make made need want let know tell say said send sent give take bring put
just also too very now then soon today tonight tomorrow later again still yet please pls ok okay sure yes
no not any some all every thing things something one day week time bit lot
hai hain hoon ho tha thi the ke ka ki ko se mein mai main tu tum aap hum ye yeh woh wo kya bhi toh na nahi
haan ji bhai yaar bro sir abhi kal aaj parso pakka sorry thanks thank dunga dungi karunga karungi kar karo
de do le lo dena lena hua gaya gayi raha rahi wala wali
""".split())


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOP}


def _same_promise(a, b):
    """Fallback for duplicates the model still produces ("Book the homestay" twice). Sharing one word is not
    enough ("technician visit" vs "senior technician"), unless one side is a single word ("Send plumber")."""
    wa, wb = _words(a), _words(b)
    if not (wa and wb):
        return False
    shared = len(wa & wb)
    return shared / min(len(wa), len(wb)) >= 0.6 and (shared >= 2 or min(len(wa), len(wb)) == 1)


def _promised_to_someone_else(c, thread, me_names) -> bool:
    """In a group, "Neel, I'll return your charger" is between Tanya and Neel; it isn't ME's business."""
    to = str(c.get("promised_to") or "").strip().lower()
    mine = {"me", "group", "everyone", "all", "us", "the group", thread.lower()} | {n.lower() for n in me_names}
    return bool(to) and to not in mine and not any(to in n.lower() or n.lower() in to for n in me_names)


RESCHEDULE = re.compile(r"\b(sorry|pakka|forgot|slipped|abhi tak|still|again|phir se|next time)\b", re.IGNORECASE)
COMPLETED = re.compile(r"\b(completed|done|sent|paid|delivered|visited|returned|received|finished|mil gay\w*|"
                       r"aa gay\w*|ho gaya|kar di\w*|bhej di\w*)\b", re.IGNORECASE)


def _rescheduled(conn, thread, p, row, what) -> bool:
    """"Sorry ji, Monday pakka bhej dunga" moves the plumber promise; "We will send a senior technician" after
    "service completed" is a new one. Same promiser, a shared word, an apology or reschedule phrase, and
    nothing in between saying the first one happened."""
    if not RESCHEDULE.search(row["text"]):
        return False
    # What the reschedule is about: its own words, plus the message it replies to ("plumber nahi aaya abhi tak").
    before = conn.execute("SELECT sender, text FROM messages WHERE thread = ? AND ts < ? ORDER BY ts DESC LIMIT 1",
                          (thread, row["ts"])).fetchone()
    about = _words(what) | _words(row["text"]) | (_words(before["text"]) if before and before["sender"] != row["sender"] else set())
    if not (_words(p["what"]) | _words(p["quote"] or "")) & about:
        return False
    between = conn.execute("SELECT text FROM messages WHERE thread = ? AND ts > ? AND ts < ?",
                           (thread, p["created_ts"], row["ts"])).fetchall()
    return not any(COMPLETED.search(m["text"]) and not m["text"].rstrip().endswith("?") for m in between)


def _save(conn, thread, row, c, masker, me_names) -> int:
    """Stores one promise. The owner comes from who sent the message, not from the model; a promise that
    repeats an open one in the same chat just moves that one's due date."""
    owner = "me" if row["is_me"] else "them"
    if owner == "them" and _promised_to_someone_else(c, thread, me_names):
        return 0
    what = re.sub(r"\bME\b", "me", masker.unmask(c.get("what", "")).strip())
    what = what[:1].upper() + what[1:]
    said_on = date.fromisoformat(row["ts"][:10])
    quote = masker.unmask(c.get("quote", ""))
    due = _check_weekday(_not_in_past(_valid_date(c.get("due")), said_on), said_on, quote)
    if due == said_on.isoformat() and not SAME_DAY.search(row["text"]):
        due = None  # the model copied the message date although no time was given
    if owner == "them":
        counterparty = row["sender"]
    else:
        counterparty = masker.unmask(c.get("counterparty") or "")
        if not counterparty or counterparty.upper() == "ME" or counterparty in me_names \
                or counterparty.lower() in ("group", "everyone", "all", "the group"):
            counterparty = thread
        # A request followed by my "sure, will do" is one promise, mine: drop the request read as theirs.
        since = (datetime.fromisoformat(row["ts"]) - timedelta(days=1)).isoformat()
        for p in conn.execute("SELECT c.id, c.what, m.text FROM commitments c JOIN messages m ON m.id = c.message_id"
                              " WHERE c.thread = ? AND c.owner = 'them' AND c.status = 'open'"
                              " AND c.created_ts >= ? AND c.created_ts < ?", (thread, since, row["ts"])).fetchall():
            if _same_promise(p["what"], what) and not FIRST_PERSON.search(p["text"]):
                conn.execute("DELETE FROM commitments WHERE id = ?", (p["id"],))
    for p in conn.execute("SELECT * FROM commitments WHERE thread = ? AND owner = ? AND status = 'open'", (thread, owner)):
        if _same_promise(p["what"], what) or _rescheduled(conn, thread, p, row, what):
            if due and (not p["due"] or due > p["due"]):
                conn.execute("UPDATE commitments SET due = ?, status_note = ? WHERE id = ?",
                             (due, f"rescheduled on {row['ts'][:10]}", p["id"]))
            return 0
    amount, currency = _money(c)
    weight = c.get("weight") if c.get("weight") in ("high", "normal", "low") else "normal"
    if weight == "low" and NEVER_MINOR.search(what):
        weight = "normal"  # hiding a late "send the password" is worse than showing a trivial one
    if amount:
        weight = "high"  # money always matters
    cur = conn.execute(
        "INSERT OR IGNORE INTO commitments (message_id, thread, source, owner, counterparty, what, due,"
        " quote, created_ts, checked_until, amount, currency, weight) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row["id"], thread, row["source"], owner, counterparty, what, due,
         quote.strip() or row["text"][:160], row["ts"], row["ts"], amount, currency, weight))
    return cur.rowcount


WEEKDAYS = {
    # Full names only: "sun cream" or "sat down" must not read as a day.
    "monday": 0, "somvar": 0, "tuesday": 1, "mangalvar": 1, "wednesday": 2, "budhvar": 2, "thursday": 3,
    "guruvar": 3, "friday": 4, "shukravar": 4, "saturday": 5, "shanivar": 5, "sunday": 6, "ravivar": 6, "itvaar": 6,
}
DAY_WORD = re.compile(r"\b(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b", re.IGNORECASE)


def _not_in_past(due, said_on: date):
    """A promise can't be due before it was made. A slipped month ("2026-09-03" for 3 October, said on 29
    September) is rolled forward to the first such date after the message; anything else is dropped."""
    if not due or date.fromisoformat(due) >= said_on:
        return due
    d = date.fromisoformat(due)
    for months in range(1, 3):
        m = d.month - 1 + months
        try:
            moved = d.replace(year=d.year + m // 12, month=m % 12 + 1)
        except ValueError:
            continue
        if moved >= said_on:
            return moved.isoformat() if (moved - said_on).days <= 45 else None
    return None


def _check_weekday(due, said_on: date, quote: str):
    """If the promise names one weekday and the model's date isn't that weekday, or isn't after the message
    ("Sunday ko de dunga" said on a Sunday means next Sunday), use that weekday's next occurrence."""
    days = {WEEKDAYS[d.lower()] for d in DAY_WORD.findall(quote or "")}
    # Only correct a date the model gave: one message can hold two promises ("I'll handle the car. Will send
    # the itinerary by Sunday"), so a weekday in the quote isn't enough to invent a date.
    if len(days) != 1 or not due:
        return due
    want = days.pop()
    if due:
        d = date.fromisoformat(due)
        if d.weekday() == want and d > said_on:
            return due
    ahead = (want - said_on.weekday()) % 7 or 7
    return (said_on + timedelta(days=ahead)).isoformat()


NEVER_MINOR = re.compile(r"\b(send|share|return|pay|transfer|book|intro\w*|forward|submit|order|fix|repair|refund|"
                         r"deliver|lend|renew|register|apply|file|bhej\w*|wapas|lauta\w*)\b", re.IGNORECASE)
PAYING = re.compile(r"\b(pay\w*|paid|transfer\w*|send|sent|return\w*|refund\w*|credit\w*|owe\w*|repay\w*|back|"
                    r"bhej\w*|de dunga|de dungi|dena|wapas|lauta\w*|gpay|upi|neft)\b", re.IGNORECASE)


def _money(c):
    # The model's amount only stands if the promise is actually about paying ("Inform me if amount exceeds
    # 20k" is a condition, not ₹20,000 owed).
    if not PAYING.search(f"{c.get('what', '')} {c.get('quote', '')}"):
        return None, None
    try:
        amount = float(str(c.get("amount")).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None, None
    if amount <= 0:
        return None, None
    return amount, (str(c.get("currency") or "INR").upper()[:3])


def _valid_date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


PARALLEL = 4


def scan(conn, days: int | None = None, progress=None) -> int:
    """Scans every chat with new messages, a few chats at a time. A chat that fails is left unscanned and
    retried next time; running out of budget stops the scan."""
    since = config.today() - timedelta(days=days or config.SCAN_DAYS)
    threads = [r["thread"] for r in conn.execute(
        "SELECT DISTINCT thread FROM messages WHERE scanned = 0 AND ts >= ?", (since.isoformat(),))]
    conn.commit()
    done = []

    def one(thread):
        with store.connect() as own:  # each worker gets its own connection
            try:
                return scan_thread(own, thread, since)
            except llm.BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad chat must not stop the rest
                own.rollback()
                print(f"  skipped {thread!r} for now: {exc}")
                return 0
            finally:
                done.append(thread)
                if progress:
                    progress(len(done), len(threads))

    with ThreadPoolExecutor(PARALLEL) as pool:
        return sum(pool.map(one, threads))


EXPIRE_DAYS = 30


def expire_quiet(conn, today: date | None = None) -> int:
    """Open promises that went quiet (due over a month ago, or undated and a month old) stop nagging."""
    cutoff = ((today or config.today()) - timedelta(days=EXPIRE_DAYS)).isoformat()
    cur = conn.execute(
        "UPDATE commitments SET status = 'expired', status_note = 'no activity for 30 days' WHERE status = 'open'"
        " AND ((due IS NOT NULL AND due < ?) OR (due IS NULL AND created_ts < ?))", (cutoff, cutoff))
    return cur.rowcount
