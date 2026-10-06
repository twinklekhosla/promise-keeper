import argparse
import os
import time
from datetime import datetime
from pathlib import Path

from . import config, ingest, nudge, pipeline, store

# Bundled datasets: the showcase demo, and a noisier "everyday" benchmark. Each gets its own database
# and a pinned "today" so late and upcoming items are stable.
DATASETS = {
    "demo": (config.ROOT / "demo", "2026-10-02"),
    "bench": (config.ROOT / "bench", "2026-10-09"),
    # The same story as bench in English, with a few Hinglish lines: used for the video and the live demo.
    "showcase": (config.ROOT / "showcase", "2026-10-09"),
}
DEMO_DIR, DEMO_TODAY = DATASETS["demo"]


def use_demo(name="demo"):
    global DEMO_DIR, DEMO_TODAY
    DEMO_DIR, DEMO_TODAY = DATASETS[name]
    config.DB_PATH = config.ROOT / "data" / f"{name}.db"
    os.environ["PK_TODAY"] = DEMO_TODAY


def cmd_init(a):
    with store.connect() as conn:
        store.set_setting(conn, "me_names", a.name)
        store.set_setting(conn, "me_emails", a.email or "")
    print(f"You are: {a.name}" + (f" <{a.email}>" if a.email else ""))


def cmd_ingest(a):
    with store.connect() as conn:
        for p in a.paths:
            me = ingest.ensure_me(conn, Path(p))
            if me:
                print(f'Recognised you as "{me}" from your chats (change with: init --name "...")')
        if not store.get_setting(conn, "me_names"):
            raise SystemExit('Couldn\'t tell which sender is you. Run: init --name "Your Name" (as it shows in your chats)')
        for p in a.paths:
            print(f"{p}: {ingest.ingest(conn, Path(p))} new messages")


def cmd_scan(a):
    with store.connect() as conn:
        r = pipeline.run(conn, progress=lambda d, t: print(f"  scanning {d}/{t} chats", end="\r", flush=True))
        print(" " * 40, end="\r")
        print(f"New promises: {r['found']}. Updates: {r['updated']}. Retired (went quiet): {r['expired']}. "
              f"Spent so far: ${r['spent_usd']:.4f}")


def cmd_digest(a):
    with store.connect() as conn:
        text = nudge.digest_text(nudge.digest(conn))
    print(text)
    if a.notify:
        print("Sent via:", ", ".join(nudge.notify(text)) or "nothing")


def cmd_draft(a):
    with store.connect() as conn:
        print(nudge.draft(conn, a.id))


def cmd_estimate(a):
    """Dry run on export files: how many messages would be sent and roughly what it would cost. No model calls."""
    from datetime import timedelta
    from . import extract, sources
    from .sources.whatsapp import is_skippable
    since = (config.today() - timedelta(days=a.days)).isoformat()
    threads, seen = {}, set()
    for p in a.paths:
        for m in sources.read_path(Path(p)):
            key = (m["thread"], m["ts"], m["sender"], m["text"])  # the same message from a duplicate export
            if not is_skippable(m["text"]) and m["ts"] >= since and key not in seen:
                seen.add(key)
                threads.setdefault(m["thread"], []).append(m)
    total = sent = calls = 0
    for msgs in threads.values():
        msgs.sort(key=lambda m: m["ts"])
        rows = [{**m, "id": str(i)} for i, m in enumerate(msgs)]
        total += len(rows)
        for i in range(0, len(rows), extract.WINDOW):
            picked = extract._with_context(rows[i:i + extract.WINDOW], rows[i:i + extract.WINDOW])
            if picked:
                sent += len(picked)
                calls += 1
    usd = sent * 0.0005
    print(f"{len(threads)} chats, {total} messages in the last {a.days} days")
    print(f"Would send {sent} ({sent / max(total, 1):.0%}) to the model, masked, in about {calls} requests")
    print(f"Rough first-scan cost: ${usd:.2f} (later scans only look at new messages)")


def cmd_ask(a):
    from .ask import ask
    with store.connect() as conn:
        r = ask(conn, " ".join(a.question))
    print(r["answer"])


def cmd_list(a):
    with store.connect() as conn:
        for c in conn.execute("SELECT * FROM commitments ORDER BY status, COALESCE(due, '9999')"):
            who = "I owe" if c["owner"] == "me" else "Owed to me"
            print(f"#{c['id']:<3} {c['status']:<7} {who:<10} {c['due'] or '-':<10} {c['counterparty']}: {c['what']}")


def cmd_spend(a):
    with store.connect() as conn:
        rows = conn.execute("SELECT purpose, COUNT(*) n, SUM(prompt_tokens) pin, SUM(completion_tokens) pout,"
                            " SUM(usd) usd FROM ledger GROUP BY purpose").fetchall()
        for r in rows:
            print(f"{r['purpose']:<8} {r['n']:>4} calls  {r['pin']:>8} in  {r['pout']:>8} out  ${r['usd']:.4f}")
        print(f"Total ${store.spent_usd(conn):.4f} of ${config.BUDGET_USD:.2f} budget")


def cmd_serve(a):
    import uvicorn
    from .web import app
    uvicorn.run(app, host="127.0.0.1", port=a.port)


def cmd_whatsapp(a):
    """Live sync with WhatsApp for Mac: pick the chats to follow. Only those are ever read."""
    import json
    from .sources import whatsapp_live
    if not whatsapp_live.available():
        raise SystemExit("WhatsApp for Mac isn't set up: install it, link your phone, then try again.")
    with store.connect() as conn:
        if a.action == "chats":
            print("\n".join(whatsapp_live.chats()))
        elif a.action == "pick":
            store.set_setting(conn, "whatsapp_chats", json.dumps(a.names, ensure_ascii=False))
            print("Following:", ", ".join(a.names) or "nothing")
        elif a.action == "sync":
            print(f"{ingest.sync_whatsapp(conn)} new messages")
        else:
            print("Following:", ", ".join(ingest.live_chats(conn)) or 'nothing yet (whatsapp pick "Chat name" ...)')
    if a.action == "sync":
        cmd_scan(a)  # after the sync is saved, so the scan's own connections aren't blocked


def cmd_watch(a):
    """Always-on mode: follows the picked WhatsApp chats every minute, re-reads the export folders every
    --every minutes, scans when something is new, and sends the digest once a day."""
    hour, minute = (int(x) for x in a.digest_at.split(":"))
    tick = 0
    while True:
        with store.connect() as conn:
            added = ingest.sync_whatsapp(conn)
            if tick % a.every == 0:
                for p in a.paths:
                    ingest.ensure_me(conn, Path(p))
                    added += ingest.ingest(conn, Path(p), only_changed=True)
            if added and (store.get_setting(conn, "me_names") or ingest.live_chats(conn)):
                pipeline.run(conn)
            now = datetime.now()
            if (now.hour, now.minute) >= (hour, minute) and store.get_setting(conn, "last_digest") != now.date().isoformat():
                nudge.notify(nudge.digest_text(nudge.digest(conn)))
                store.set_setting(conn, "last_digest", now.date().isoformat())
        tick += 1
        time.sleep(60)


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.promisekeeper.watch</string>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string><string>-m</string><string>promise_keeper</string>
    <string>watch</string>{folders}<string>--digest-at</string><string>{at}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def cmd_install(a):
    """Writes a macOS login item that runs `watch` in the background. Prints the command to switch it on."""
    import sys
    folders = "".join(f"<string>{Path(p).expanduser().resolve()}</string>" for p in a.paths)
    target = Path.home() / "Library" / "LaunchAgents" / "com.promisekeeper.watch.plist"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(PLIST.format(root=config.ROOT, python=sys.executable, folders=folders, at=a.digest_at,
                                   log=config.DB_PATH.parent / "watch.log"))
    print(f"Wrote {target}\nTurn it on:   launchctl load {target}\nTurn it off:  launchctl unload {target}")


def cmd_demo(a):
    use_demo(a.dataset)
    if config.DB_PATH.exists() and a.reset:
        with store.connect() as conn:
            conn.execute("DELETE FROM messages")
            conn.execute("DELETE FROM commitments")
    with store.connect() as conn:
        store.set_setting(conn, "me_names", "Aarav Mehta,Aarav")
        store.set_setting(conn, "me_emails", "aarav.mehta@example.com")
        print(f"Loaded {ingest.ingest(conn, DEMO_DIR / 'chats')} demo messages (today is pinned to {DEMO_TODAY})")
        r = pipeline.run(conn)
        print(f"Promises found: {r['found']}, status updates: {r['updated']}")
        print()
        print(nudge.digest_text(nudge.digest(conn)))
        print(f"\nNebius spend so far: ${store.spent_usd(conn):.4f}")


def cmd_eval(a):
    import json as _json
    from .evaluate import evaluate
    use_demo(a.dataset)
    with store.connect() as conn:
        report = evaluate(conn, DEMO_DIR / "gold.json")
    print(_json.dumps(report, indent=1, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(prog="promise-keeper", description="Keeps track of the promises in your chats.")
    parser.add_argument("--demo", action="store_true", help="use the bundled demo inbox instead of your own data")
    parser.add_argument("--bench", action="store_true", help="use the bundled everyday benchmark inbox")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="tell the app who you are")
    p.add_argument("--name", required=True, help="your name as it appears in chats; comma-separate variants")
    p.add_argument("--email", help="your email addresses, comma-separated")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("ingest", help="load WhatsApp .txt exports, .eml or .mbox files, or folders of them")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_ingest)
    sub.add_parser("scan", help="find new promises and check old ones").set_defaults(func=cmd_scan)
    p = sub.add_parser("digest", help="what's late or coming up")
    p.add_argument("--notify", action="store_true", help="also send it as a notification")
    p.set_defaults(func=cmd_digest)
    p = sub.add_parser("draft", help="draft a follow-up message for a promise")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_draft)
    sub.add_parser("list", help="all promises").set_defaults(func=cmd_list)
    p = sub.add_parser("ask", help='ask in plain words, e.g. ask "who owes me money?"')
    p.add_argument("question", nargs="+")
    p.set_defaults(func=cmd_ask)
    p = sub.add_parser("estimate", help="free dry run: what would be sent and roughly what it would cost")
    p.add_argument("paths", nargs="+")
    p.add_argument("--days", type=int, default=60)
    p.set_defaults(func=cmd_estimate)
    sub.add_parser("spend", help="Nebius spend so far").set_defaults(func=cmd_spend)
    p = sub.add_parser("serve", help="open the local dashboard")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser("whatsapp", help="live sync with WhatsApp for Mac: pick which chats to follow")
    p.add_argument("action", nargs="?", choices=["status", "chats", "pick", "sync"], default="status")
    p.add_argument("names", nargs="*", help='for pick: chat names exactly as WhatsApp shows them')
    p.set_defaults(func=cmd_whatsapp)
    p = sub.add_parser("watch", help="always-on: follow WhatsApp, re-scan folders, send a daily digest")
    p.add_argument("paths", nargs="*")
    p.add_argument("--every", type=int, default=30, help="minutes between re-reading export folders")
    p.add_argument("--digest-at", default="09:00")
    p.set_defaults(func=cmd_watch)
    p = sub.add_parser("install", help="run watch in the background at login (macOS)")
    p.add_argument("paths", nargs="*", help="folders to watch, e.g. ~/Downloads (WhatsApp chats you picked are always followed)")
    p.add_argument("--digest-at", default="09:00")
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("demo", help="load the demo chats and show the digest")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--dataset", choices=DATASETS, default="demo")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("eval", help="score the demo run against the hand-labelled answers")
    p.add_argument("--dataset", choices=DATASETS, default="demo")
    p.set_defaults(func=cmd_eval)

    a = parser.parse_args()
    if a.bench:
        use_demo("bench")
    elif a.demo:
        use_demo("demo")
    a.func(a)
