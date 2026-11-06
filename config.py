import os
import re
from typing import Dict

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DEFAULT_CHANNEL_ID = os.getenv("DEFAULT_CHANNEL_ID", "").strip()
DEFAULT_GUILD_ID = os.getenv("DEFAULT_GUILD_ID", "").strip()
OWNER_USER_ID = os.getenv("OWNER_USER_ID", "").strip()
DEFAULT_BOARD = os.getenv("DEFAULT_BOARD", "HardwareSale").strip()

SUPPORTED_LANGUAGES = {"zh_TW", "en"}
DEFAULT_LANGUAGE = "zh_TW"


def _parse_language(raw: str) -> str:
    cleaned = (raw or "").strip().lower()
    if cleaned in ("en", "en_us", "en-us", "english"):
        return "en"
    return DEFAULT_LANGUAGE


BOT_LANGUAGE = _parse_language(os.getenv("BOT_LANGUAGE", DEFAULT_LANGUAGE))

POLL_MIN = int(os.getenv("POLL_INTERVAL_MIN", "90"))
POLL_MAX = int(os.getenv("POLL_INTERVAL_MAX", "150"))

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
GROQ_FAST_MODEL = os.getenv("GROQ_FAST_MODEL", "openai/gpt-oss-20b").strip()

DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "bot_data.db")).strip()
LOG_PATH = os.getenv("LOG_PATH", os.path.join(BASE_DIR, "bot.log")).strip()

# Active Groq models (each has its own 8,000 TPM / 1,000 RPD free-tier bucket)
ALTERNATING_MODELS_POOL = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
]

# Global limits to protect PTT & Groq
MAX_GLOBAL_ACTIVE_BOARDS = 25
MAX_KEYWORD_LEN = 50
MAX_FUZZY_DESC_LEN = 200
ARTICLE_CACHE_TTL_SEC = 600        # 10 minutes
AI_CACHE_TTL_SEC = 3600            # 60 minutes
MEMORY_TTL_SEC = 1800              # 30 minutes
MEMORY_MAX_TURNS = 6
DEDUPE_WINDOW_DAYS = 3
MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER = 5

# Per-tier Quotas
TIER_QUOTAS: Dict[str, Dict[str, int]] = {
    "free": {
        "max_keywords": 10,
        "max_fuzzy_rules": 3,
        "max_boards": 5,
        "daily_ai_chats": 20,
        "daily_scans": 8,
    },
    "friend": {
        "max_keywords": 30,
        "max_fuzzy_rules": 8,
        "max_boards": 5,
        "daily_ai_chats": 60,
        "daily_scans": 25,
    },
    "owner": {
        "max_keywords": 999,
        "max_fuzzy_rules": 999,
        "max_boards": 20,
        "daily_ai_chats": 9999,
        "daily_scans": 9999,
    },
}

ALLOWED_CATEGORIES = {"賣", "徵", "換", "情報", "ALL"}

MENTION_PATTERN = re.compile(
    r"(@everyone|@here|<@[!&]?\d+>|<#\d+>|<a?:[a-zA-Z0-9_]+:\d+>)",
    re.IGNORECASE,
)


def sanitize_user_input(text: str, max_len: int = 200) -> str:
    """
    Strip Discord mention tokens, channel tags, custom emojis and enforce length limit.
    """
    cleaned = MENTION_PATTERN.sub("", (text or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]
