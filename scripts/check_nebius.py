"""Checks that the Nebius key works with one small Nemotron call."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI

from promise_keeper import config

if not config.NEBIUS_API_KEY:
    sys.exit("NEBIUS_API_KEY is empty. Paste your key into .env first.")

client = OpenAI(base_url=config.NEBIUS_BASE_URL, api_key=config.NEBIUS_API_KEY)
reply = client.chat.completions.create(
    model=config.MODEL,
    max_tokens=600,
    messages=[{"role": "user", "content": 'In one short sentence: is "kal tak bhej dunga" a promise?'}],
)
print("Nemotron:", (reply.choices[0].message.content or "").strip())
