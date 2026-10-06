"""Local dashboard. Binds to 127.0.0.1 only; nothing is exposed to the network."""
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, ingest, jobs, live, llm, nudge, store
from .sources import whatsapp_live

@asynccontextmanager
async def lifespan(app):
    live.start()  # follows the picked WhatsApp chats while the dashboard runs
    yield


app = FastAPI(title="Promise Keeper", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"


class StatusIn(BaseModel):
    status: str


class SnoozeIn(BaseModel):
    days: int = 2


class MeIn(BaseModel):
    name: str


class AskIn(BaseModel):
    question: str


class ChatsIn(BaseModel):
    chats: list[str]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/state")
def state():
    today = config.today()
    with store.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT c.*, m.text AS source_text, m.sender AS source_sender FROM commitments c"
            " LEFT JOIN messages m ON m.id = c.message_id ORDER BY COALESCE(c.due, '9999'), c.created_ts")]
        groups = nudge.digest(conn, today)
        return {
            "today": today.isoformat(),
            "me": store.get_setting(conn, "me_names").split(",")[0],
            "commitments": rows,
            "digest": nudge.digest_text(groups),
            "counts": {k: len(v) for k, v in groups.items()},
            "messages": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "sent_to_model": conn.execute("SELECT COUNT(*) FROM messages WHERE sent = 1").fetchone()[0],
            "spent_usd": round(store.spent_usd(conn), 4),
            "budget_usd": config.BUDGET_USD,
            "job": jobs.state,
            "money": nudge.money(conn),
            "whatsapp": {"available": whatsapp_live.available(), "chats": ingest.live_chats(conn), **live.state},
        }


@app.get("/api/version")
def version():
    """Changes whenever new messages arrive or a scan starts, progresses or ends; the page polls this cheap
    call and reloads the full state only when it changes."""
    j = jobs.state
    return {"v": f"{live.state['added']}:{j['running']}:{j['done']}:{j['finished_at']}"}


@app.post("/api/commitments/{cid}/status")
def set_status(cid: int, body: StatusIn):
    if body.status not in ("open", "done", "dropped", "dismissed"):
        raise HTTPException(400, "status must be open, done, dropped or dismissed")
    note = "you said this isn't a promise" if body.status == "dismissed" else "marked by you"
    with store.connect() as conn:
        conn.execute("UPDATE commitments SET status = ?, status_note = ? WHERE id = ?", (body.status, note, cid))
    return {"ok": True}


@app.post("/api/commitments/{cid}/snooze")
def snooze(cid: int, body: SnoozeIn):
    until = (config.today() + timedelta(days=body.days)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE commitments SET snooze_until = ? WHERE id = ?", (until, cid))
    return {"ok": True, "until": until}


def _reply_links(conn, cid, text):
    """One-tap sending: WhatsApp opens with the text ready; email opens as a reply to the right person."""
    from urllib.parse import quote
    c = conn.execute("SELECT * FROM commitments WHERE id = ?", (cid,)).fetchone()
    links = {"whatsapp": "https://wa.me/?text=" + quote(text)}
    if c and c["source"] == "email":
        row = conn.execute("SELECT sender_email FROM messages WHERE thread = ? AND is_me = 0 AND sender_email IS NOT NULL"
                           " ORDER BY ts DESC LIMIT 1", (c["thread"],)).fetchone()
        subject = "Re: " + c["thread"].removeprefix("Email: ")
        to = row["sender_email"] if row else ""
        links["email"] = f"mailto:{quote(to)}?subject={quote(subject)}&body={quote(text)}"
    return links


@app.post("/api/commitments/{cid}/draft")
def draft(cid: int):
    with store.connect() as conn:
        try:
            text = nudge.draft(conn, cid)
            return {"message": text, "links": _reply_links(conn, cid, text)}
        except KeyError:
            raise HTTPException(404, "no such promise")
        except llm.BudgetExceeded as exc:
            raise HTTPException(402, str(exc))


def _imports_folder() -> Path:
    # One folder per database, so chats dropped into one dashboard never affect another.
    return config.DB_PATH.parent / "imports" / config.DB_PATH.stem


@app.post("/api/import")
async def import_files(files: list[UploadFile] = File(...)):
    """Drop WhatsApp exports (.txt or iPhone .zip) and .eml/.mbox files; they're saved locally and scanned."""
    folder = _imports_folder()
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        name = Path(f.filename or "upload").name
        if Path(name).suffix.lower() not in (".txt", ".zip", ".eml", ".mbox"):
            continue
        dest = folder / name
        dest.write_bytes(await f.read())
        saved.append(dest)
    if not saved:
        raise HTTPException(400, "Drop WhatsApp exports (.txt or .zip) or email files (.eml, .mbox).")
    with store.connect() as conn:
        me = ingest.ensure_me(conn, folder)
        if not store.get_setting(conn, "me_names"):
            return {"added": 0, "needs_name": True}
        added = sum(ingest.ingest(conn, p) for p in saved)
    return {"added": added, "files": len(saved), "me": me, "scanning": jobs.start()}


@app.post("/api/scan")
def scan():
    return {"scanning": jobs.start()}


@app.post("/api/me")
def set_me(body: MeIn):
    with store.connect() as conn:
        store.set_setting(conn, "me_names", body.name.strip())
        folder = _imports_folder()
        added = sum(ingest.ingest(conn, p) for p in sorted(folder.glob("*"))) if folder.exists() else 0
    return {"ok": True, "added": added, "scanning": jobs.start() if added else False}


@app.post("/api/ask")
def ask_endpoint(body: AskIn):
    from .ask import ask
    if not body.question.strip():
        raise HTTPException(400, "Ask a question")
    with store.connect() as conn:
        try:
            return ask(conn, body.question.strip()[:500])
        except llm.BudgetExceeded as exc:
            raise HTTPException(402, str(exc))


@app.get("/api/whatsapp/chats")
def whatsapp_chats():
    """Chat names for the picker, read from WhatsApp for Mac on this machine. No messages are read here."""
    if not whatsapp_live.available():
        raise HTTPException(404, "WhatsApp for Mac isn't set up on this Mac")
    return {"chats": whatsapp_live.chats()}


@app.post("/api/whatsapp")
def pick_whatsapp_chats(body: ChatsIn):
    """Saves which chats to keep in sync. Only these are ever read."""
    names = list(dict.fromkeys(n for n in body.chats if n.strip()))
    with store.connect() as conn:
        store.set_setting(conn, "whatsapp_chats", json.dumps(names, ensure_ascii=False))
    added = live.sync_now()
    return {"chats": names, "added": added, "scanning": jobs.start() if added else False}
