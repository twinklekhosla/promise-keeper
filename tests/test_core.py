from datetime import date
from pathlib import Path

from promise_keeper import config, extract, ingest, nudge, store
from promise_keeper.privacy import Masker
from promise_keeper.sources import email_export, whatsapp

DEMO = Path(__file__).resolve().parent.parent / "demo" / "chats"


def test_whatsapp_android_export():
    msgs = list(whatsapp.read(DEMO / "WhatsApp Chat with Rohit Sharma.txt"))
    assert msgs[0]["thread"] == "Rohit Sharma"
    assert msgs[0]["sender"] == "Rohit Sharma"
    assert msgs[0]["ts"] == "2026-09-16T20:02:00"
    assert any("kal tak bhej dunga" in m["text"] for m in msgs)


def test_whatsapp_iphone_format_and_multiline(tmp_path):
    f = tmp_path / "WhatsApp Chat - Team.txt"
    f.write_text("[02/10/26, 9:05:12 AM] Priya: I'll send it\nby noon\n[02/10/26, 9:06:00 PM] Me: ok\n")
    msgs = list(whatsapp.read(f))
    assert msgs[0]["text"] == "I'll send it\nby noon"
    assert msgs[1]["ts"] == "2026-10-02T21:06:00"
    assert msgs[0]["thread"] == "Team"


def test_email_strips_quoted_reply():
    msg = next(email_export.read_eml(DEMO / "email" / "interiors-2.eml"))
    assert msg["thread"] == "Email: Revised quote for 2BHK interiors"
    assert "floor plan by EOD tomorrow" in msg["text"]
    assert "site visit" not in msg["text"]


def test_masking_round_trip():
    m = Masker()
    text = "Call 98450 12345 or +91 98860 44321, mail vikram@studiovr.example, policy 4402198763"
    masked = m.mask(text)
    for secret in ("98450 12345", "98860 44321", "vikram@studiovr.example", "4402198763"):
        assert secret not in masked
    assert m.unmask(masked) == text


def test_promise_cues():
    assert extract.CUES.search("deck kal tak bhej dunga")
    assert extract.CUES.search("I'll book the turf tonight")
    assert not extract.CUES.search("Jeete raho")


def test_ingest_and_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setenv("PK_TODAY", "2026-10-02")
    with store.connect() as conn:
        store.set_setting(conn, "me_names", "Aarav Mehta")
        assert ingest.ingest(conn, DEMO / "WhatsApp Chat with Ishita.txt") == 7  # media line skipped
        assert ingest.ingest(conn, DEMO / "WhatsApp Chat with Ishita.txt") == 0  # idempotent
        mine = conn.execute("SELECT COUNT(*) FROM messages WHERE is_me = 1").fetchone()[0]
        assert mine == 3
        rows = [("m", "me", "2026-09-30", "late thing"), ("m", "me", "2026-10-04", "soon thing"),
                ("m", "them", "2026-09-29", "they owe"), ("m", "me", "2026-11-30", "far thing")]
        for i, (mid, owner, due, what) in enumerate(rows):
            conn.execute("INSERT INTO commitments (message_id, thread, owner, counterparty, what, due, quote, created_ts)"
                         " VALUES (?, 'Ishita', ?, 'Ishita', ?, ?, '', '2026-09-20T10:00:00')", (f"{mid}{i}", owner, what, due))
        groups = nudge.digest(conn, date(2026, 10, 2))
    assert [c["what"] for c in groups["i_owe_overdue"]] == ["late thing"]
    assert [c["what"] for c in groups["i_owe_soon"]] == ["soon thing"]
    assert [c["what"] for c in groups["owed_to_me_overdue"]] == ["they owe"]


def _zip_export(tmp_path, name, text):
    import zipfile
    z = tmp_path / f"WhatsApp Chat - {name}.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("_chat.txt", text)
    return z


def test_iphone_zip_system_lines_and_edits(tmp_path):
    text = ("‎[28/09/26, 9:00:00 PM] Rohit Sharma: ‎Messages and calls are end-to-end encrypted.\n"
            "[28/09/26, 9:01:00 PM] Rohit Sharma: I'll send the deck tomorrow <This message was edited>\n"
            "[28/09/26, 9:02:00 PM] Rohit Sharma: ‎image omitted\n"
            "[28/09/26, 9:03:00 PM] Aarav Mehta: I added the photos to the drive\n")
    msgs = [m for m in whatsapp.read(_zip_export(tmp_path, "Rohit Sharma", text)) if not whatsapp.is_skippable(m["text"])]
    assert [m["text"] for m in msgs] == ["I'll send the deck tomorrow", "I added the photos to the drive"]
    assert msgs[0]["thread"] == "Rohit Sharma"


def test_group_system_events_are_dropped(tmp_path):
    f = tmp_path / "WhatsApp Chat with Gang.txt"
    f.write_text("01/10/26, 9:00 am - Neel: Hi\n[01/10/26, 9:01:00] Gang: Neel added Varun and Tanya\n"
                 "01/10/26, 9:02 am - Varun: Varun left\n01/10/26, 9:03 am - Tanya: I left my keys at yours\n")
    texts = [m["text"] for m in whatsapp.read(f)]
    assert "Neel added Varun and Tanya" not in texts
    assert "I left my keys at yours" in texts


def test_month_first_dates_detected(tmp_path):
    f = tmp_path / "WhatsApp Chat with Sam.txt"
    f.write_text("9/28/26, 9:41 PM - Sam: I'll pay you back Friday\n10/1/26, 8:00 AM - Me: ok\n")
    assert [m["ts"][:10] for m in whatsapp.read(f)] == ["2026-09-28", "2026-10-01"]


def test_guess_me_from_one_to_one_chats():
    from promise_keeper.sources import guess_me
    assert guess_me(DEMO) == "Aarav Mehta"


def test_weekday_sanity_check():
    from promise_keeper.extract import _check_weekday
    sun = date(2026, 9, 20)
    assert _check_weekday("2026-09-20", sun, "Sunday ko pakka de dunga") == "2026-09-27"   # same day -> next week
    assert _check_weekday("2026-09-24", date(2026, 9, 22), "Saturday ko aake theek kar dunga") == "2026-09-26"
    assert _check_weekday("2026-10-02", date(2026, 9, 21), "Friday subah station pe rahunga") == "2026-10-02"  # explicit date kept
    assert _check_weekday(None, date(2026, 9, 18), "I'll handle the car. Will send the itinerary by Sunday") is None
    assert _check_weekday("2026-09-30", date(2026, 9, 25), "share it by Wednesday") == "2026-09-30"
    assert _check_weekday("2026-10-01", date(2026, 9, 28), "after salary on the 1st") == "2026-10-01"  # no weekday
    assert _check_weekday(None, date(2026, 9, 21), "I'll bring the sun cream") is None


def test_phone_number_senders_are_masked():
    from promise_keeper.extract import _line
    m = Masker()
    line = _line("m1", {"ts": "2026-10-01T09:00:00", "is_me": 0, "sender": "+91 98765 43210", "text": "I'll pay tomorrow"}, masker=m)
    assert "98765" not in line and "<PHONE_1>" in line


def test_due_never_before_the_promise():
    from promise_keeper.extract import _not_in_past
    assert _not_in_past("2026-09-03", date(2026, 9, 29)) == "2026-10-03"
    assert _not_in_past("2026-10-05", date(2026, 9, 29)) == "2026-10-05"
    assert _not_in_past("2025-01-01", date(2026, 9, 29)) is None
    assert _not_in_past(None, date(2026, 9, 29)) is None


def test_only_changed_files_are_reread(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    src = tmp_path / "exports"
    src.mkdir()
    (src / "WhatsApp Chat with Ishita.txt").write_text((DEMO / "WhatsApp Chat with Ishita.txt").read_text())
    with store.connect() as conn:
        store.set_setting(conn, "me_names", "Aarav Mehta")
        assert ingest.ingest(conn, src, only_changed=True) == 7
        assert list(ingest._changed_files(conn, src)) == []          # nothing to re-read
        with open(src / "WhatsApp Chat with Ishita.txt", "a") as f:
            f.write("01/10/26, 9:00 am - Ishita: Sending the photos tonight, pakka\n")
        assert ingest.ingest(conn, src, only_changed=True) == 1      # only the new message


def test_dismissed_lines_are_shown_to_the_model(tmp_path, monkeypatch):
    from promise_keeper.extract import _not_promises
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    with store.connect() as conn:
        assert _not_promises(conn, Masker()) == ""
        conn.execute("INSERT INTO commitments (message_id, thread, owner, what, quote, status) VALUES "
                     "('x', 'T', 'them', 'Wake me at 6', 'I''ll wake you up at 6', 'dismissed')")
        assert "I'll wake you up at 6" in _not_promises(conn, Masker())


def test_devanagari_promise_is_noticed():
    assert extract.CUES.search("ठीक है, कल भेज दूंगा")


def test_guess_my_email():
    from promise_keeper.sources import guess_my_email
    assert guess_my_email(DEMO / "email") == "aarav.mehta@example.com"


def test_email_reply_link_goes_to_the_other_person(tmp_path, monkeypatch):
    from promise_keeper import web
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    with store.connect() as conn:
        store.set_setting(conn, "me_names", "Aarav Mehta")
        store.set_setting(conn, "me_emails", "aarav.mehta@example.com")
        ingest.ingest(conn, DEMO / "email")
        mid = conn.execute("SELECT id FROM messages WHERE thread LIKE '%interiors%' AND is_me = 0").fetchone()[0]
        conn.execute("INSERT INTO commitments (message_id, thread, source, owner, counterparty, what, quote, created_ts)"
                     " VALUES (?, 'Email: Revised quote for 2BHK interiors', 'email', 'them', 'Vikram Rao', 'Send quote', '', '2026-09-21')", (mid,))
        cid = conn.execute("SELECT MAX(id) FROM commitments").fetchone()[0]
        links = web._reply_links(conn, cid, "Hi Vikram, any update?")
    assert links["email"].startswith("mailto:vikram%40studiovr.example?subject=Re%3A%20Revised%20quote")
    assert "Hi%20Vikram" in links["email"] and links["whatsapp"].startswith("https://wa.me/?text=")


def test_duplicate_download_names_are_the_same_chat(tmp_path):
    for name in ("WhatsApp Chat with Rohit Sharma (2).txt", "WhatsApp Chat with Rohit Sharma 2.txt"):
        assert whatsapp._thread_name(tmp_path / name) == "Rohit Sharma"
    assert whatsapp._thread_name(tmp_path / "WhatsApp Chat with Flat 12.txt") == "Flat 12"


def test_sending_and_returning_are_never_hidden_as_minor():
    from promise_keeper.extract import NEVER_MINOR
    for what in ("Send Ishita the Netflix password", "Return the drill", "Send the pics", "Book Diwali tickets", "Intro Sameer to Karthik"):
        assert NEVER_MINOR.search(what), what
    for what in ("Make kebabs", "Come around 7:30", "Delete it", "Have breakfast ready"):
        assert not NEVER_MINOR.search(what), what
