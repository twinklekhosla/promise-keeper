"""The one routine everything runs: find new promises, check old ones, retire the ones that went quiet."""
from . import extract, store, track


def run(conn, progress=None) -> dict:
    found = extract.scan(conn, progress=progress)
    changed = track.track(conn)
    expired = extract.expire_quiet(conn)
    conn.commit()
    return {"found": found, "updated": changed, "expired": expired, "spent_usd": round(store.spent_usd(conn), 4)}
