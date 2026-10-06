"""Nemotron on Nebius Token Factory, behind a hard budget cap and a local response cache."""
import hashlib
import json
import os

from openai import OpenAI

from . import config, store


class BudgetExceeded(RuntimeError):
    pass


class Truncated(RuntimeError):
    """The answer hit the length limit (Nemotron's reasoning counts towards it). Send less and retry."""


def _client():
    if not config.NEBIUS_API_KEY:
        raise RuntimeError("NEBIUS_API_KEY is empty. Paste your key into .env.")
    return OpenAI(base_url=config.NEBIUS_BASE_URL, api_key=config.NEBIUS_API_KEY)


def chat_json(conn, system: str, user: str, purpose: str, max_tokens: int = 12000, model: str | None = None) -> dict:
    """Returns the model's JSON answer. Identical requests are served from the cache for free."""
    model = model or config.MODEL
    key = hashlib.sha256(json.dumps([model, system, user]).encode()).hexdigest()
    cached = conn.execute("SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
    if cached and not os.environ.get("PK_NO_CACHE"):
        return json.loads(cached["response"])

    price_in, price_out = config.PRICES.get(model, (1.0, 3.0))
    worst_case = (len(system + user) / 3 * price_in + max_tokens * price_out) / 1e6
    spent = store.spent_usd(conn)
    if spent + worst_case > config.BUDGET_USD:
        raise BudgetExceeded(f"Budget cap reached: ${spent:.4f} spent of ${config.BUDGET_USD:.2f}.")

    reply = _client().chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    usage = reply.usage
    usd = (usage.prompt_tokens * price_in + usage.completion_tokens * price_out) / 1e6
    conn.execute(
        "INSERT INTO ledger (purpose, model, prompt_tokens, completion_tokens, usd) VALUES (?, ?, ?, ?, ?)",
        (purpose, model, usage.prompt_tokens, usage.completion_tokens, usd),
    )
    conn.commit()

    text = reply.choices[0].message.content or ""
    if reply.choices[0].finish_reason == "length":
        raise Truncated(f"{purpose}: answer cut off after {usage.completion_tokens} tokens")
    try:
        result = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError as exc:
        raise RuntimeError(f"Model did not return JSON: {text[:200]}") from exc
    conn.execute("INSERT OR REPLACE INTO llm_cache (key, response) VALUES (?, ?)", (key, json.dumps(result)))
    return result


def _guard(conn, model, prompt_chars, max_tokens):
    price_in, price_out = config.PRICES.get(model, (1.0, 3.0))
    worst_case = (prompt_chars / 3 * price_in + max_tokens * price_out) / 1e6
    spent = store.spent_usd(conn)
    if spent + worst_case > config.BUDGET_USD:
        raise BudgetExceeded(f"Budget cap reached: ${spent:.4f} spent of ${config.BUDGET_USD:.2f}.")
    return price_in, price_out


def chat_tools(conn, messages: list, tools: list, purpose: str, max_tokens: int = 6000, model: str | None = None):
    """One tool-calling turn (for the Ask agent). Returns the assistant message; cost goes on the same ledger."""
    model = model or config.MODEL
    price_in, price_out = _guard(conn, model, len(json.dumps(messages)), max_tokens)
    reply = _client().chat.completions.create(model=model, max_tokens=max_tokens, temperature=0,
                                              messages=messages, tools=tools)
    usage = reply.usage
    conn.execute(
        "INSERT INTO ledger (purpose, model, prompt_tokens, completion_tokens, usd) VALUES (?, ?, ?, ?, ?)",
        (purpose, model, usage.prompt_tokens, usage.completion_tokens,
         (usage.prompt_tokens * price_in + usage.completion_tokens * price_out) / 1e6))
    conn.commit()
    return reply.choices[0].message
