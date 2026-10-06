"""A stand-in for WhatsApp for Mac's database, with made-up chats: for tests and for demoing live sync
without touching real chats.

    python scripts/fake_whatsapp.py create data/fake_whatsapp.sqlite
    PK_WHATSAPP_DB=data/fake_whatsapp.sqlite python -m promise_keeper serve
    python scripts/fake_whatsapp.py say data/fake_whatsapp.sqlite "Rohan Kapoor" Rohan "I'll send the invoice by Friday"

Only the tables and columns Promise Keeper reads are created, with the same names and meanings.
"""
import sqlite3
import sys
import time

APPLE_EPOCH = 978307200
SCHEMA = """
CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZSESSIONTYPE INTEGER, ZPARTNERNAME VARCHAR,
    ZREMOVED INTEGER DEFAULT 0, ZLASTMESSAGEDATE TIMESTAMP, ZCONTACTJID VARCHAR);
CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZCONTACTNAME VARCHAR,
    ZFIRSTNAME VARCHAR, ZMEMBERJID VARCHAR);
CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZISFROMME INTEGER, ZMESSAGEDATE TIMESTAMP,
    ZMESSAGETYPE INTEGER, ZGROUPEVENTTYPE INTEGER, ZTEXT VARCHAR, ZPUSHNAME VARCHAR, ZGROUPMEMBER INTEGER);
"""
# (chat, one-to-one?, members)
CHATS = [("Rohan Kapoor", True, []), ("Flat 4B", False, ["Meera", "Kabir"]), ("Memes", False, ["Dev"])]
# (chat, sender or "me", hours ago, text, message type)
MESSAGES = [
    ("Rohan Kapoor", "Rohan Kapoor", 50, "Hey, did you get a chance to look at the floor plan?", 0),
    ("Rohan Kapoor", "me", 49, "Not yet, I'll review it and send comments by tomorrow evening", 0),
    ("Rohan Kapoor", "Rohan Kapoor", 49, "Perfect, thanks", 0),
    ("Flat 4B", "Meera", 30, "Meera changed the group description", 6),
    ("Flat 4B", "Meera", 26, "Electricity bill is 2,400 this month", 0),
    ("Flat 4B", "Kabir", 26, "I'll pay my share tonight", 0),
    ("Flat 4B", "Kabir", 25, "", 1),  # a photo
    ("Memes", "Dev", 5, "Monday mood 😂", 0),
    ("Memes", "Dev", 4, "I'll kill you if you post that again 😂", 0),
]


def now() -> float:
    return time.time() - APPLE_EPOCH


def create(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    members = {}
    for pk, (name, direct, people) in enumerate(CHATS, 1):
        conn.execute("INSERT INTO ZWACHATSESSION (Z_PK, ZSESSIONTYPE, ZPARTNERNAME, ZLASTMESSAGEDATE) VALUES (?, ?, ?, ?)",
                     (pk, 0 if direct else 1, name, now()))
        for person in people:
            cur = conn.execute("INSERT INTO ZWAGROUPMEMBER (ZCHATSESSION, ZCONTACTNAME, ZMEMBERJID) VALUES (?, ?, ?)",
                               (pk, person, f"9100000000{len(members)}@s.whatsapp.net"))
            members[(name, person)] = cur.lastrowid
    for chat, sender, hours, text, kind in MESSAGES:
        _add(conn, chat, sender, text, now() - hours * 3600, kind)
    conn.commit()
    print(f"Wrote {path}: {len(CHATS)} made-up chats, {len(MESSAGES)} messages")


def _add(conn, chat, sender, text, at, kind=0):
    session, direct = conn.execute("SELECT Z_PK, ZSESSIONTYPE = 0 FROM ZWACHATSESSION WHERE ZPARTNERNAME = ?", (chat,)).fetchone()
    member = None
    if sender != "me" and not direct:
        row = conn.execute("SELECT Z_PK FROM ZWAGROUPMEMBER WHERE ZCHATSESSION = ? AND ZCONTACTNAME = ?", (session, sender)).fetchone()
        member = row[0] if row else conn.execute(
            "INSERT INTO ZWAGROUPMEMBER (ZCHATSESSION, ZCONTACTNAME) VALUES (?, ?)", (session, sender)).lastrowid
    conn.execute("INSERT INTO ZWAMESSAGE (ZCHATSESSION, ZISFROMME, ZMESSAGEDATE, ZMESSAGETYPE, ZGROUPEVENTTYPE, ZTEXT, ZGROUPMEMBER)"
                 " VALUES (?, ?, ?, ?, 2, ?, ?)", (session, int(sender == "me"), at, kind, text or None, member))
    conn.execute("UPDATE ZWACHATSESSION SET ZLASTMESSAGEDATE = ? WHERE Z_PK = ?", (at, session))


def say(path, chat, sender, text):
    conn = sqlite3.connect(path)
    _add(conn, chat, sender, text, now())
    conn.commit()
    print(f"{chat} · {sender}: {text}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "create":
        create(sys.argv[2])
    elif len(sys.argv) == 6 and sys.argv[1] == "say":
        say(*sys.argv[2:])
    else:
        raise SystemExit(__doc__)
