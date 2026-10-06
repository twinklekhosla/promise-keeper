"""Builds docs/demo/index.html: the dashboard as a static page on the made-up English "showcase" inbox, with
pre-written drafts and example answers, for GitHub Pages. Uses the local cache, so re-running is nearly free."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from promise_keeper import cli, store  # noqa: E402

cli.use_demo("showcase")
from promise_keeper import nudge, web  # noqa: E402
from promise_keeper.ask import ask  # noqa: E402

QUESTIONS = [
    "Who owes me money?",
    "What's late from my side?",
    "Did Kabir return my drill?",
    "Maa ko kya promise kiya tha?",
]

snapshot = web.state()
snapshot["job"] = {"running": False}
drafts, reply_to, answers = {}, {}, []
with store.connect() as conn:
    for c in snapshot["commitments"]:
        if c["status"] == "open":
            drafts[c["id"]] = nudge.draft(conn, c["id"])
            if c["source"] == "email":
                reply_to[c["id"]] = web._reply_links(conn, c["id"], "")["email"].split("?")[0].removeprefix("mailto:")
    for q in QUESTIONS:
        r = ask(conn, q)
        answers.append({"q": q, "a": r["answer"], "tools": r["tools"]})
    spent = store.spent_usd(conn)

page = (ROOT / "promise_keeper" / "static" / "index.html").read_text()
data = json.dumps({"state": snapshot, "drafts": drafts, "reply_to": reply_to, "answers": answers}, ensure_ascii=False)
page = page.replace("<script>\n", f"<script>window.STATIC = {data};</script>\n<script>\n", 1)
page = page.replace("<title>Promise Keeper</title>", "<title>Promise Keeper · live demo</title>", 1)
out = ROOT / "docs" / "demo" / "index.html"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(page)
print(f"Wrote {out} ({out.stat().st_size // 1024} KB): {len(drafts)} drafts, {len(answers)} answers. Spend now ${spent:.4f}")
