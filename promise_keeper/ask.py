"""Ask Promise Keeper: a Nemotron agent that answers questions about your promises using local tools.

The tools run on this machine and return only what the question needs; contact details are masked before
anything goes to the model.
"""
import json
from datetime import date

from . import config, llm, nudge, store
from .privacy import Masker

TOOLS = [
    {"type": "function", "function": {
        "name": "find_promises",
        "description": "List promises. Use for 'what did I promise X', 'who owes me', 'what's late', 'what's coming up'.",
        "parameters": {"type": "object", "properties": {
            "owner": {"type": "string", "enum": ["me", "them", "any"],
                      "description": "'me' = ME promised, 'them' = someone promised ME"},
            "status": {"type": "string", "enum": ["open", "done", "dropped", "any"]},
            "person": {"type": "string", "description": "name or chat to filter by, e.g. 'Rohit', 'Maa'"},
            "only_late": {"type": "boolean"},
            "only_money": {"type": "boolean"},
            "include_minor": {"type": "boolean", "description": "also list tiny household items (normally hidden)"}}}}},
    {"type": "function", "function": {
        "name": "money_summary",
        "description": "Totals of open money promises: how much people owe ME and how much ME owes.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "search_messages",
        "description": "Keyword search in ME's chats, for questions the promise list can't answer.",
        "parameters": {"type": "object", "properties": {
            "words": {"type": "string", "description": "a few keywords, e.g. 'homestay booked'"},
            "person": {"type": "string"}}, "required": ["words"]}}},
]

SYSTEM = """You are Promise Keeper, ME's private assistant for the promises in their chats and emails.
Today is {today}. ME is {me}.
Use the tools to look things up; never guess. Answer briefly and concretely, in the same language as the
question: English question, English answer; Hinglish question, Hinglish answer. Mention due dates and how late things are. When you refer to a
promise, add its id like [#12]. Write short lines or "- " bullets; no tables and no headings. Contact details appear as placeholders such as <PHONE_1>; don't repeat them."""


def _promise_rows(conn, owner="any", status="open", person=None, only_late=False, only_money=False, include_minor=False):
    q, args = "SELECT * FROM commitments WHERE status NOT IN ('dismissed')", []
    if not include_minor:
        q += " AND COALESCE(weight, 'normal') != 'low'"
    if owner in ("me", "them"):
        q += " AND owner = ?"; args.append(owner)
    if status and status != "any":
        q += " AND status = ?"; args.append(status)
    if person:
        q += " AND (counterparty LIKE ? OR thread LIKE ?)"; args += [f"%{person}%", f"%{person}%"]
    if only_late:
        q += " AND due IS NOT NULL AND due < ?"; args.append(config.today().isoformat())
    if only_money:
        q += " AND amount IS NOT NULL"
    return conn.execute(q + " ORDER BY COALESCE(due, '9999') LIMIT 40", args).fetchall()


def _run_tool(conn, name, args, masker):
    today = config.today()
    if name == "find_promises":
        rows = _promise_rows(conn, **{k: v for k, v in args.items() if k in
                                      ("owner", "status", "person", "only_late", "only_money", "include_minor")})
        out = []
        for r in rows:
            late = (today - date.fromisoformat(r["due"])).days if r["due"] and r["status"] == "open" else None
            out.append({"id": r["id"], "who": "ME" if r["owner"] == "me" else r["counterparty"],
                        "to": r["counterparty"] if r["owner"] == "me" else "ME", "what": r["what"],
                        "due": r["due"], "days_late": late if late and late > 0 else 0, "status": r["status"],
                        "amount": r["amount"], "currency": r["currency"], "chat": r["thread"]})
        return out
    if name == "money_summary":
        return nudge.money(conn)
    if name == "search_messages":
        words = [w for w in str(args.get("words", "")).split() if len(w) > 2][:5]
        if not words:
            return []
        q = "SELECT * FROM messages WHERE " + " AND ".join("text LIKE ?" for _ in words)
        params = [f"%{w}%" for w in words]
        if args.get("person"):
            q += " AND (thread LIKE ? OR sender LIKE ?)"; params += [f"%{args['person']}%"] * 2
        rows = conn.execute(q + " ORDER BY ts DESC LIMIT 8", params).fetchall()
        return [{"date": r["ts"][:16], "chat": r["thread"], "from": "ME" if r["is_me"] else masker.mask(r["sender"]),
                 "text": masker.mask(r["text"][:300])} for r in rows]
    return {"error": f"unknown tool {name}"}


def ask(conn, question: str) -> dict:
    """Runs the agent loop (at most 5 tool rounds) and returns the answer and the promise ids it cited."""
    me = store.get_setting(conn, "me_names").split(",")[0].strip() or "the user"
    masker = Masker()
    messages = [{"role": "system", "content": SYSTEM.format(today=f"{config.today():%A %d %B %Y}", me=me)},
                {"role": "user", "content": question}]
    used = []
    for _ in range(5):
        reply = llm.chat_tools(conn, messages, TOOLS, purpose="ask")
        if not reply.tool_calls:
            answer = masker.unmask(reply.content or "").strip()
            return {"answer": answer, "tools": used}
        messages.append({"role": "assistant", "content": reply.content or "",
                         "tool_calls": [t.model_dump() for t in reply.tool_calls]})
        for call in reply.tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except ValueError:
                args = {}
            used.append({"tool": call.function.name, "args": args})
            result = _run_tool(conn, call.function.name, args, masker)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})
    return {"answer": "I couldn't finish looking that up. Try asking more specifically.", "tools": used}
