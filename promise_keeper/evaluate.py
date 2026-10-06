"""Scores what the app found against the hand-labelled demo answer key (demo/gold.json)."""
import json
import re
from datetime import date

STOP = {"the", "a", "an", "to", "for", "of", "on", "and", "my", "his", "her", "s", "with", "by", "it", "in", "send", "share"}


def _words(text):
    # 4-letter stems, so "laddoo" matches "laddoos" and "pushed" matches "push"
    return {w[:4] for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP}


def _close(a, b, days=1):
    if a is None or b is None:
        return a is None and b is None
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= days


def evaluate(conn, gold_path):
    gold = json.loads(open(gold_path).read())
    found = [dict(r) for r in conn.execute("SELECT * FROM commitments")]
    used, matches = set(), []
    for g in gold:
        best, best_score = None, 0
        for f in found:
            if f["id"] in used or f["thread"] != g["thread"] or f["owner"] != g["owner"]:
                continue
            score = len(_words(g["what"]) & _words(f["what"] + " " + (f["quote"] or "")))
            if score > best_score:
                best, best_score = f, score
        if best:
            used.add(best["id"])
        matches.append((g, best))

    # "optional" items are borderline: finding them isn't a false alarm, missing them isn't a miss.
    required = [(g, f) for g, f in matches if not g.get("optional")]
    hit = [(g, f) for g, f in required if f]
    optional_found = sum(1 for g, f in matches if g.get("optional") and f)
    judged = len(found) - optional_found
    report = {
        "gold": len(required),
        "found": len(found),
        "matched": len(hit),
        "optional_found": optional_found,
        "recall": round(len(hit) / len(required), 3),
        "precision": round(len(hit) / judged, 3) if judged else 0.0,
        "due_correct": sum(_close(g["due"], f["due"]) for g, f in hit),
        "status_correct": sum(g["status"] == f["status"] for g, f in hit),
        "missed": [f"{g['thread']}: {g['what']}" for g, f in required if not f],
        "extra": [f"{f['thread']}: {f['what']}" for f in found if f["id"] not in used],
        "wrong_due": [f"{g['thread']}: {g['what']} (want {g['due']}, got {f['due']})" for g, f in hit if not _close(g["due"], f["due"])],
        "hidden_as_minor": [f"{g['thread']}: {g['what']}" for g, f in hit if f.get("weight") == "low"],
        "wrong_status": [f"{g['thread']}: {g['what']} (want {g['status']}, got {f['status']})" for g, f in hit if g["status"] != f["status"]],
    }
    return report
