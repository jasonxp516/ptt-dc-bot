import os
import re
from typing import Any, Dict

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _parse_bool(val: Any, default: bool = False) -> bool:
    """安全解析布林值，支援 1/0, true/false, yes/no, on/off"""
    if val is None:
        return default
    cleaned = str(val).strip().lower()
    if cleaned in ("true", "1", "yes", "y", "on", "enable", "enabled", "t"):
        return True
    if cleaned in ("false", "0", "no", "n", "off", "disable", "disabled", "f"):
        return False
    return default


def _parse_int(val: Any, default: int) -> int:
    """安全解析整數，遇到非法字元時安全回退至預設值"""
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------
# 多平台啟用開關 (Multi-Platform Activation Flags - Milestone 3)
# ---------------------------------------------------------------------
ENABLE_DISCORD = _parse_bool(
    os.getenv("ENABLE_DISCORD", os.getenv("DISCORD_ENABLED", "true")), default=True
)
ENABLE_TELEGRAM = _parse_bool(
    os.getenv("ENABLE_TELEGRAM", os.getenv("TELEGRAM_ENABLED", "false")), default=False
)
ENABLE_EMAIL = _parse_bool(
    os.getenv("ENABLE_EMAIL", os.getenv("EMAIL_ENABLED", "false")), default=False
)

# ---------------------------------------------------------------------
# Telegram 平台組態 (Telegram Bot Configuration)
# ---------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_ADMIN_USER_ID = os.getenv("TELEGRAM_ADMIN_USER_ID", "").strip()
# Telegram user IDs (comma separated) the bot ignores completely
TELEGRAM_BLOCKED_USER_IDS = os.getenv("TELEGRAM_BLOCKED_USER_IDS", "").strip()

# ---------------------------------------------------------------------
# Email SMTP 平台組態 (Email SMTP Configuration)
# ---------------------------------------------------------------------
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = _parse_int(os.getenv("SMTP_PORT", "587"), 587)
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_USE_TLS = _parse_bool(os.getenv("SMTP_USE_TLS", "true"), default=True)
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip() or SMTP_USER
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "PTT 智慧追蹤機器人").strip()
EMAIL_TO = os.getenv("EMAIL_TO", os.getenv("EMAIL_TARGET_RECIPIENT", "")).strip()

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

# Lower bound of 30s protects PTT from a mistyped interval; MAX is never below MIN.
POLL_MIN = max(30, _parse_int(os.getenv("POLL_INTERVAL_MIN", "90"), 90))
POLL_MAX = max(POLL_MIN, _parse_int(os.getenv("POLL_INTERVAL_MAX", "150"), 150))

# N1: Articles whose PTT post timestamp (M.<epoch>.A.xxx) is older than this are treated as
# history: marked seen silently and never pushed. Prevents low-traffic boards from re-pushing
# old index-page posts after the 14-day seen_articles cleanup, and bounds outage backfill.
MAX_ARTICLE_AGE_HOURS = _parse_int(os.getenv("MAX_ARTICLE_AGE_HOURS", "24"), 24)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
GROQ_FAST_MODEL = os.getenv("GROQ_FAST_MODEL", "openai/gpt-oss-20b").strip()

DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "bot_data.db")).strip()
LOG_PATH = os.getenv("LOG_PATH", os.path.join(BASE_DIR, "bot.log")).strip()
HEARTBEAT_FILE = os.getenv(
    "HEARTBEAT_FILE",
    os.path.join(os.path.dirname(os.path.abspath(DB_PATH)) or BASE_DIR, "heartbeat"),
).strip()

def _build_models_pool() -> list:
    """
    Active Groq models (each has its own 8,000 TPM / 1,000 RPD free-tier bucket).
    R03: GROQ_MODELS (comma separated) replaces the whole pool, so a model Groq retires can be
    swapped from .env. Otherwise the pool is GROQ_MODEL, the built-in secondary model and
    GROQ_FAST_MODEL (identical to the previous fixed list when those are left at their defaults).
    """
    raw = os.getenv("GROQ_MODELS", "").replace(";", ",")
    pool = [m.strip() for m in raw.split(",") if m.strip()]
    if not pool:
        pool = [GROQ_MODEL, "qwen/qwen3.8-27b", GROQ_FAST_MODEL]
    deduped = []
    for m in pool:
        if m and m not in deduped:
            deduped.append(m)
    return deduped or ["openai/gpt-oss-120b"]


ALTERNATING_MODELS_POOL = _build_models_pool()

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
