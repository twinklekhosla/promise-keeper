import json
import sys
from pathlib import Path

from promise_keeper import config, ingest, store
from promise_keeper.sources import whatsapp_live

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fake_whatsapp  # noqa: E402


def _setup(tmp_path, monkeypatch):
    wa = tmp_path / "ChatStorage.sqlite"
    fake_whatsapp.create(str(wa))
    monkeypatch.setattr(config, "WHATSAPP_DB", wa)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    return wa


def test_only_picked_chats_are_read(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert whatsapp_live.chats()[0] in {"Rohan Kapoor", "Flat 4B", "Memes"}
    msgs = list(whatsapp_live.read(["Flat 4B"]))
    assert {m["thread"] for m in msgs} == {"Flat 4B"}
    # The system event and the photo are skipped; group senders get their contact name.
    assert [(m["sender"], m["text"]) for m in msgs] == [
        ("Meera", "Electricity bill is 2,400 this month"), ("Kabir", "I'll pay my share tonight")]


def test_one_to_one_senders_and_me(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    msgs = list(whatsapp_live.read(["Rohan Kapoor"]))
    assert [m["sender"] for m in msgs] == ["Rohan Kapoor", "You", "Rohan Kapoor"]
    assert msgs[0]["ts"] < msgs[1]["ts"]


def test_nothing_is_read_until_chats_are_picked(tmp_path, monkeypatch):
    wa = _setup(tmp_path, monkeypatch)
    with store.connect() as conn:
        assert ingest.sync_whatsapp(conn) == 0
        store.set_setting(conn, "whatsapp_chats", json.dumps(["Rohan Kapoor"]))
        assert ingest.sync_whatsapp(conn) == 3
        assert conn.execute("SELECT is_me FROM messages WHERE sender = 'You'").fetchone()[0] == 1
        assert ingest.sync_whatsapp(conn) == 0  # already seen
        fake_whatsapp.say(str(wa), "Rohan Kapoor", "Rohan Kapoor", "Any update?")
        fake_whatsapp.say(str(wa), "Memes", "Dev", "not followed")
        assert ingest.sync_whatsapp(conn) == 1


def test_export_and_live_copies_of_a_message_are_one(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    f = tmp_path / "WhatsApp Chat with Rohan Kapoor.txt"
    msgs = list(whatsapp_live.read(["Rohan Kapoor"]))
    lines = [f"{m['ts'][8:10]}/{m['ts'][5:7]}/{m['ts'][2:4]}, {m['ts'][11:16]} - "
             f"{'Aarav' if m['sender'] == 'You' else m['sender']}: {m['text']}" for m in msgs]
    f.write_text("\n".join(lines) + "\n")
    with store.connect() as conn:
        store.set_setting(conn, "me_names", "Aarav")
        assert ingest.ingest(conn, f) == 3
        store.set_setting(conn, "whatsapp_chats", json.dumps(["Rohan Kapoor"]))
        assert ingest.sync_whatsapp(conn) == 0


def test_sync_reads_only_new_rows(tmp_path, monkeypatch):
    wa = _setup(tmp_path, monkeypatch)
    with store.connect() as conn:
        store.set_setting(conn, "whatsapp_chats", json.dumps(["Rohan Kapoor", "Flat 4B"]))
        ingest.sync_whatsapp(conn)
        seen = json.loads(store.get_setting(conn, "whatsapp_seen"))
    assert list(whatsapp_live.read(["Rohan Kapoor", "Flat 4B"], after=seen)) == []
    fake_whatsapp.say(str(wa), "Flat 4B", "Kabir", "Paid")
    assert [m["text"] for m in whatsapp_live.read(["Rohan Kapoor", "Flat 4B"], after=seen)] == ["Paid"]
