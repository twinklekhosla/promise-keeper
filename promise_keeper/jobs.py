"""A single background scan at a time, so the dashboard stays responsive while Nemotron works."""
import threading
import time

from . import llm, pipeline, store

_lock = threading.Lock()
state = {"running": False, "stage": "", "done": 0, "total": 0, "result": None, "error": None, "finished_at": None,
         "trigger": None}


def start(trigger: str = "user") -> bool:
    """trigger is "live" when new WhatsApp messages started the scan, so the page can announce what changed."""
    if not _lock.acquire(blocking=False):
        return False
    state.update(running=True, stage="Reading your chats", done=0, total=0, result=None, error=None, trigger=trigger)

    def progress(done, total):
        state.update(stage="Finding promises", done=done, total=total)

    def work():
        try:
            with store.connect() as conn:
                state["result"] = pipeline.run(conn, progress=progress)
        except llm.BudgetExceeded as exc:
            state["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - shown in the dashboard
            state["error"] = f"Scan failed: {exc}"
        finally:
            state.update(running=False, stage="", finished_at=time.time())
            _lock.release()

    threading.Thread(target=work, daemon=True).start()
    return True
