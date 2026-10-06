import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY", "")
MODEL = os.environ.get("PK_MODEL", "nvidia/nemotron-3-super-120b-a12b")
# Deciding whether a promise was kept can use a different (e.g. larger) model.
# Ultra by default: a wrong "3 days late" is the most annoying mistake, and tracking calls are few and small.
TRACK_MODEL = os.environ.get("PK_TRACK_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")

# USD per 1M tokens (input, output), from the Nebius Token Factory price list.
PRICES = {
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 3.00),
}
# Hard cap on total Nebius spend. Calls are refused once the ledger reaches it.
BUDGET_USD = float(os.environ.get("PK_BUDGET_USD", "3.0"))

# How far back a scan looks, in days before "today". Raise it to import older chats.
SCAN_DAYS = int(os.environ.get("PK_SCAN_DAYS", "60"))

DB_PATH = Path(os.environ.get("PK_DB", ROOT / "data" / "promise_keeper.db"))

TELEGRAM_TOKEN = os.environ.get("PK_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("PK_TELEGRAM_CHAT_ID", "")


def today() -> date:
    """The date the app treats as today. PK_TODAY pins it (used by the demo)."""
    pinned = os.environ.get("PK_TODAY")
    return date.fromisoformat(pinned) if pinned else date.today()

# WhatsApp for Mac's local database, read for live sync (only the chats you pick).
WHATSAPP_DB = Path(os.environ.get(
    "PK_WHATSAPP_DB",
    Path.home() / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"))
