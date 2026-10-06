"""Live sync with WhatsApp for Mac while the dashboard is open: new messages in the picked chats are read
within seconds, and a scan follows (at most one every 10 seconds, so a burst of messages costs one scan)."""
import threading
import time

from . import config, ingest, jobs, store

POLL_SECONDS = 2
MIN_GAP_SECONDS = 10
state = {"last_sync": None, "added": 0, "error": None}


def _stamp():
    """Changes whenever WhatsApp writes: it writes to the -wal file first, then folds it into the database."""
    files = [config.WHATSAPP_DB, config.WHATSAPP_DB.with_name(config.WHATSAPP_DB.name + "-wal")]
    return tuple(f.stat().st_mtime_ns if f.exists() else 0 for f in files)


def sync_now() -> int:
    with store.connect() as conn:
        added = ingest.sync_whatsapp(conn)
    state.update(last_sync=time.time(), added=state["added"] + added, error=None)
    return added


def _loop():
    seen, pending, last_scan = None, False, 0.0
    while True:
        try:
            stamp = _stamp()
            if stamp != seen:
                seen = stamp
                pending = sync_now() > 0 or pending
            if pending and time.time() - last_scan >= MIN_GAP_SECONDS and jobs.start("live"):
                pending, last_scan = False, time.time()
        except Exception as exc:  # noqa: BLE001 - shown in the dashboard; the loop keeps going
            state["error"] = f"WhatsApp sync: {exc}"
        time.sleep(POLL_SECONDS)


def start():
    threading.Thread(target=_loop, daemon=True).start()
