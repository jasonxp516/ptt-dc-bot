import glob
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from config import (
    AI_CACHE_TTL_SEC,
    ALLOWED_CATEGORIES,
    ARTICLE_CACHE_TTL_SEC,
    DEDUPE_WINDOW_DAYS,
    DEFAULT_GUILD_ID,
    MAX_FUZZY_DESC_LEN,
    MAX_GLOBAL_ACTIVE_BOARDS,
    MAX_KEYWORD_LEN,
    MEMORY_MAX_TURNS,
    MEMORY_TTL_SEC,
    OWNER_USER_ID,
    TIER_QUOTAS,
    sanitize_user_input,
)
from timeutil import now_tpe, now_tpe_str, today_tpe_str

logger = logging.getLogger("storage")

_USAGE_KIND_MAP = {
    "ai": ("daily_ai_chats", 20),
    "ai_chat": ("daily_ai_chats", 20),
    "scan": ("daily_scans", 20),
    "board_add": ("daily_board_adds", 5),
}


# F01: retry schedule for notifications that could not be sent (seconds after each failure)
PENDING_DELIVERY_BACKOFF_SEC = (120, 300, 900, 1800, 3600)
PENDING_DELIVERY_MAX_AGE_SEC = 86400


def compute_dedupe_hash(board: str, author: str, title: str) -> str:
    norm_title = re.sub(r"[\s\[\]/\-_、，,]+", "", title.lower())
    raw = f"{board.lower()}:{author.lower()}:{norm_title}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


# Model / capacity patterns that must NOT be mistaken for prices (Fix P1-3)
_MODEL_STRIP_PATTERNS = re.compile(
    r"(?:"
    r"(?:rtx|gtx|rx|arc|uhd|hd|gt)\s*[-_]?\s*\d{3,4}\s*(?:ti|xt|xtx|super|gre|s|m)?"
    r"|(?:i[3579]|r[3579]|ryzen\s*[3579]|ultra\s*[579])\s*[-_]?\s*\d{3,5}[a-z0-9]*"
    r"|(?:[bzhx]\d{3}[a-z]?|x\d{3}e?|z\d{3}|h\d{3}|a\d{3})"
    r"|ddr[345]\s*[-_]?\s*\d{3,4}"
    r"|\b\d{1,4}\s*(?:gb|g|tb|t|mhz|mt/s|w|瓦|mm|hz|吋|寸)\b"
    r"|\b[a-z]+[-_]\d{3,6}[a-z0-9]*\b"
    r"|\b\d{3,6}[-_][a-z]+\b"
    r")",
    re.IGNORECASE,
)


# Date / year patterns that must NOT be mistaken for prices (Phase 1.2)
_DATE_YEAR_STRIP_PATTERNS = re.compile(
    r"(?:"
    r"\b(?:199\d|20[0-3]\d)\s*年(?:度|份|購|買|購入|出廠)?"
    r"|\b(?:199\d|20[0-3]\d)\s*[/\-.]\s*\d{1,2}(?:\s*[/\-.]\s*\d{1,2})?\b"
    r"|\b\d{1,2}\s*/\s*\d{1,2}\b"
    r"|(?:保固|到期|出廠|購於|購買|購入|發票|至|到|產於|製造|年份)\s*[:：]?\s*(?:199\d|20[0-3]\d)\b"
    r")",
    re.IGNORECASE,
)

_NEGOTIABLE_NO_PRICE_REGEX = re.compile(
    r"(?:價格面議|面議|待議|站內議|來信報價|來信討論)",
    re.IGNORECASE,
)


def parse_budget(value: Any) -> Optional[int]:
    """
    Phase 1.2 / 2.5: Parse a user or LLM budget parameter into an integer TWD amount.
    Supports int, float (6500.0), '6500', '6,500', '6500元', 'NT$6,500', '$6500', '6.5k', '6.5K'.
    Returns None if value is None or empty string.
    Returns 0 if value explicitly represents 0 (used to clear budget in update_keyword).
    Raises ValueError if value cannot be parsed or is out of valid range (100..500000).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"無效的預算型別: {value}")
    if isinstance(value, (int, float)):
        ival = int(round(float(value)))
        if ival == 0:
            return 0
        if 100 <= ival <= 500000:
            return ival
        raise ValueError(f"預算金額超出合理範圍 (100 ~ 500,000 元): {value}")

    s = str(value).strip()
    if not s:
        return None

    # Normalize commas and currency wrappers
    s_clean = s.replace(",", "").replace("，", "").strip()
    if re.match(r"^(?:0|0\.0+)\s*(?:元|塊|ntd|twd|\$)?$", s_clean, re.IGNORECASE):
        return 0

    # Match k/K shorthand, e.g. '6.5k', 'NT$6.5K', '6.5k元'
    m_k = re.match(
        r"^(?:nt\$?|twd\$?|\$|約|預算|上限)?\s*(\d+(?:\.\d+)?)\s*[kK]\s*(?:元|塊|ntd|twd|以內|內)?$",
        s_clean,
        re.IGNORECASE,
    )
    if m_k:
        ival = int(round(float(m_k.group(1)) * 1000))
        if 100 <= ival <= 500000:
            return ival
        raise ValueError(f"預算金額超出合理範圍 (100 ~ 500,000 元): {value}")

    # Match plain number or number with currency prefix/suffix, e.g. '6500.0', '6500元', 'NT$6500'
    m_num = re.match(
        r"^(?:nt\$?|twd\$?|\$|約|預算|上限)?\s*(\d+(?:\.\d+)?)\s*(?:元|塊|ntd|twd|以內|內)?$",
        s_clean,
        re.IGNORECASE,
    )
    if m_num:
        ival = int(round(float(m_num.group(1))))
        if ival == 0:
            return 0
        if 100 <= ival <= 500000:
            return ival
        raise ValueError(f"預算金額超出合理範圍 (100 ~ 500,000 元): {value}")

    raise ValueError(f"無法解析預算參數 `{value}`，請輸入有效的整數金額（如 6500 或 6.5k）。")


def extract_first_price_int(price_str: Optional[str]) -> Optional[int]:
    """
    Fix P1-3 & Phase 1.2: Accurately parse the item price in TWD from a price string without
    confusing hardware model numbers (e.g. 'RTX 3060 12G 售 5800', 'i5-12400 + 3060Ti 共 15000', '5.8k')
    or dates/years (e.g. '保固至 2026/03 售 5800', '2025年購入 面交 6000元', '價格面議 2025') for prices!
    """
    if not price_str:
        return None

    s = price_str.replace(",", "").replace("，", "")
    # 1. Strip date/year context first, then strip hardware model tokens
    cleaned = _DATE_YEAR_STRIP_PATTERNS.sub(" ", s)
    cleaned = _MODEL_STRIP_PATTERNS.sub(" ", cleaned)

    # 2. Check for explicit 'k' / 'K' price shorthand (e.g. '5.8k', '6k')
    m_k = re.search(r"(?<![A-Za-z0-9])(\d{1,2}(?:\.\d{1,2})?)\s*[kK](?![A-Za-z0-9])", cleaned)
    if m_k:
        try:
            val_k = int(round(float(m_k.group(1)) * 1000))
            if 100 <= val_k <= 500000:
                return val_k
        except ValueError:
            pass

    # 3. Prioritize numbers immediately preceded or followed by currency / price markers
    m_curr = re.search(
        r"(?:(?:\$|NT\$?|TWD\$?|售價|價格|欲售|售|賣|共|只要)\s*[:：]?\s*(\d{3,6})\b"
        r"|\b(\d{3,6})\s*(?:元|塊|NTD|TWD))",
        cleaned,
        re.IGNORECASE,
    )
    if m_curr:
        num_str = m_curr.group(1) or m_curr.group(2)
        try:
            val = int(num_str)
            if 100 <= val <= 500000:
                return val
        except ValueError:
            pass

    # 4. Prioritize numbers after a colon (e.g., '2. RTX 3060TI: 5800')
    m_colon = re.search(r"[:：]\s*(\d{3,6})(?![A-Za-z0-9])", cleaned)
    if m_colon:
        try:
            val = int(m_colon.group(1))
            if 100 <= val <= 500000:
                return val
        except ValueError:
            pass

    # 5. Fallback: only accept an unambiguous single standalone number (never guess the last number
    # among multiple numbers, and never treat isolated 1990-2035 year numbers in negotiable text as price)
    if _NEGOTIABLE_NO_PRICE_REGEX.search(cleaned):
        return None

    candidates = []
    for m in re.finditer(r"(?<![A-Za-z0-9\-_])(\d{3,6})(?![A-Za-z0-9\-_])", cleaned):
        try:
            val = int(m.group(1))
            if 100 <= val <= 500000:
                candidates.append(val)
        except ValueError:
            pass

    if len(candidates) == 1:
        only_val = candidates[0]
        # If it's in the 1990..2035 year range, only accept if the entire input string was just that number
        if 1990 <= only_val <= 2035 and not re.match(r"^\s*\d{4}\s*$", s):
            return None
        return only_val
    return None


from sold_rules import is_title_marked_sold, pick_item_token


def price_for_budget_check(detail: Optional[Dict]) -> Optional[int]:
    """
    F16: Price used when comparing a keyword view against a subscriber's max_price budget.
    - Normal view: first parsed price of the (item-specific) extracted price string.
    - Ambiguous multi-item view (target item has no dedicated price line): use the LOWEST
      parsed price among all lines, so a post is only filtered when every listed price is over
      budget. The first line may belong to a different, cheaper item and must not be treated
      as this item's confirmed price.
    """
    if not detail:
        return None
    if detail.get("price_ambiguous"):
        prices = [extract_first_price_int(pl) for pl in (detail.get("raw_price_lines") or [])]
        valid = [p for p in prices if p is not None]
        return min(valid) if valid else None
    return extract_first_price_int(detail.get("extracted_price"))




class BotStorage:
    """
    Thread-safe, multi-tenant SQLite storage with WAL mode, persistent connection,
    article/AI caching, quota tracking, conversation memory, and delivery deduplication.
    """

    def __init__(
        self,
        db_path: str,
        default_channel_id: str = "",
        default_board: str = "HardwareSale",
        default_guild_id: str = DEFAULT_GUILD_ID,
    ):
        self.db_path = db_path
        self.default_channel_id = str(default_channel_id or "")
        self.default_board = default_board
        self.default_guild_id = str(default_guild_id or "000000000000000000")
        self._lock = threading.RLock()
        self._conn = self._open_connection()
        self._init_and_migrate_db()

    def _open_connection(self) -> sqlite3.Connection:
        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def _init_and_migrate_db(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            row = cur.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
            schema_ver = int(row["value"]) if row and row["value"].isdigit() else 1

            has_subs = (
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='subscribers'"
                ).fetchone()
                is not None
            )

            was_existing_v4_only = False
            if has_subs and schema_ver == 4:
                deliv_table = cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='deliveries'"
                ).fetchone()
                if deliv_table is not None:
                    deliv_cols = {
                        r["name"] for r in cur.execute("PRAGMA table_info(deliveries)").fetchall()
                    }
                    if "matched_keyword" not in deliv_cols:
                        was_existing_v4_only = True

            if schema_ver < 2 or not has_subs:
                if os.path.exists(self.db_path) and os.path.getsize(self.db_path) > 0:
                    bak_path = f"{self.db_path}.bak"
                    try:
                        self.backup_database_atomic(bak_path)
                    except Exception:
                        pass
                self._run_schema_v2_migration(cur)
                self._conn.commit()
                schema_ver = 2

            if schema_ver < 3:
                if os.path.exists(self.db_path) and os.path.getsize(self.db_path) > 0:
                    bak_v2 = f"{self.db_path}.bak.v2"
                    if not os.path.exists(bak_v2):
                        try:
                            self.backup_database_atomic(bak_v2)
                        except Exception:
                            pass
                self._run_schema_v3_migration(cur)
                self._conn.commit()
                schema_ver = 3

            if schema_ver < 4:
                if os.path.exists(self.db_path) and os.path.getsize(self.db_path) > 0:
                    bak_v3 = f"{self.db_path}.bak.v3"
                    if not os.path.exists(bak_v3):
                        try:
                            self.backup_database_atomic(bak_v3)
                        except Exception:
                            pass
                self._run_schema_v4_migration(cur)
                self._conn.commit()
                schema_ver = 4

            # Dynamic Next Migration (current_version + 1 = 5):
            # Upgrade deliveries table with matched_keyword, sold_state, edit_failed
            # while keeping settings['schema_version'] == '4' for legacy v4 test compatibility
            # and tracking current_version + 1 in PRAGMA user_version.
            u_ver_row = cur.execute("PRAGMA user_version").fetchone()
            user_ver = int(u_ver_row[0]) if u_ver_row else 0
            target_next_ver = max(schema_ver, 4) + 1
            deliv_cols_now = {
                r["name"] for r in cur.execute("PRAGMA table_info(deliveries)").fetchall()
            }
            if (
                user_ver < target_next_ver
                or "matched_keyword" not in deliv_cols_now
                or "sold_state" not in deliv_cols_now
                or "edit_failed" not in deliv_cols_now
            ):
                if was_existing_v4_only and os.path.exists(self.db_path) and os.path.getsize(self.db_path) > 0:
                    bak_v4 = f"{self.db_path}.bak.v4"
                    if not os.path.exists(bak_v4):
                        try:
                            self.backup_database_atomic(bak_v4)
                        except Exception:
                            pass
                self._run_schema_next_migration(cur, target_next_ver)
                self._conn.commit()

            # v1.3.0 (F01): additive retry queue for notifications that could not be sent.
            # Created idempotently without touching user_version, so older releases can still
            # open the database (they simply ignore the extra table).
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,
                    article_url TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    next_attempt_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '',
                    UNIQUE(subscriber_id, article_url)
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_deliveries_due "
                "ON pending_deliveries(subscriber_id, next_attempt_at)"
            )
            self._conn.commit()

    def _run_schema_v2_migration(self, cur: sqlite3.Cursor) -> None:
        now_str = now_tpe_str()

        # 1. Subscribers table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL CHECK(scope IN ('user', 'guild')),
                discord_id TEXT NOT NULL,
                display_name TEXT DEFAULT '',
                push_channel_id TEXT,
                push_mode TEXT NOT NULL DEFAULT 'keyword',
                tier TEXT NOT NULL DEFAULT 'free',
                enabled INTEGER NOT NULL DEFAULT 1,
                dm_failed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'discord',
                target_id TEXT NOT NULL DEFAULT '',
                UNIQUE(scope, discord_id)
            )
            """
        )

        legacy_ch = self.default_channel_id
        legacy_mode = "keyword"
        r_ch = cur.execute("SELECT value FROM settings WHERE key = 'push_channel_id'").fetchone()
        if r_ch and r_ch["value"]:
            legacy_ch = r_ch["value"]
        r_md = cur.execute("SELECT value FROM settings WHERE key = 'push_mode'").fetchone()
        if r_md and r_md["value"]:
            legacy_mode = r_md["value"]

        cur.execute(
            """
            INSERT OR IGNORE INTO subscribers
                (scope, discord_id, display_name, push_channel_id, push_mode, tier, enabled, created_at, platform, target_id)
            VALUES ('guild', ?, '預設伺服器', ?, ?, 'owner', 1, ?, 'discord', ?)
            """,
            (self.default_guild_id, legacy_ch, legacy_mode, now_str, legacy_ch or self.default_guild_id),
        )
        default_sub_row = cur.execute(
            "SELECT id FROM subscribers WHERE scope = 'guild' AND discord_id = ?",
            (self.default_guild_id,),
        ).fetchone()
        default_sub_id = default_sub_row["id"] if default_sub_row else 1

        # 2. Boards table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS boards (
                name TEXT PRIMARY KEY COLLATE NOCASE,
                added_at TEXT NOT NULL,
                initialized INTEGER DEFAULT 0,
                last_checked TEXT DEFAULT '尚未檢查',
                last_status TEXT DEFAULT '等待中',
                last_scanned_at INTEGER DEFAULT 0,
                consecutive_failures INTEGER DEFAULT 0
            )
            """
        )
        existing_cols = {
            r["name"] for r in cur.execute("PRAGMA table_info(boards)").fetchall()
        }
        if "last_scanned_at" not in existing_cols:
            cur.execute("ALTER TABLE boards ADD COLUMN last_scanned_at INTEGER DEFAULT 0")
        if "consecutive_failures" not in existing_cols:
            cur.execute("ALTER TABLE boards ADD COLUMN consecutive_failures INTEGER DEFAULT 0")

        b_cnt = cur.execute("SELECT COUNT(*) as cnt FROM boards").fetchone()
        if b_cnt and b_cnt["cnt"] == 0 and self.default_board:
            cur.execute(
                "INSERT OR IGNORE INTO boards (name, added_at, initialized, last_scanned_at) VALUES (?, ?, 0, 0)",
                (self.default_board, now_str),
            )

        # 3. Migrate or create keywords table
        kw_cols = {
            r["name"] for r in cur.execute("PRAGMA table_info(keywords)").fetchall()
        }
        if not kw_cols:
            cur.execute(
                """
                CREATE TABLE keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscriber_id INTEGER NOT NULL DEFAULT 1 REFERENCES subscribers(id) ON DELETE CASCADE,
                    keyword TEXT NOT NULL COLLATE NOCASE,
                    board TEXT NOT NULL DEFAULT 'HardwareSale' COLLATE NOCASE,
                    category TEXT NOT NULL DEFAULT '賣' COLLATE NOCASE,
                    max_price INTEGER DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(subscriber_id, keyword, board, category)
                )
                """
            )
        elif "subscriber_id" not in kw_cols:
            cur.execute(
                """
                CREATE TABLE keywords_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscriber_id INTEGER NOT NULL DEFAULT 1 REFERENCES subscribers(id) ON DELETE CASCADE,
                    keyword TEXT NOT NULL COLLATE NOCASE,
                    board TEXT NOT NULL DEFAULT 'HardwareSale' COLLATE NOCASE,
                    category TEXT NOT NULL DEFAULT '賣' COLLATE NOCASE,
                    max_price INTEGER DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(subscriber_id, keyword, board, category)
                )
                """
            )
            old_kws = cur.execute("SELECT id, keyword, board, category, created_at FROM keywords").fetchall()
            for okw in old_kws:
                b_val = self.default_board if okw["board"].upper() == "ALL" else okw["board"]
                cur.execute(
                    """
                    INSERT OR IGNORE INTO keywords_new (id, subscriber_id, keyword, board, category, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (okw["id"], default_sub_id, okw["keyword"], b_val, okw["category"], okw["created_at"]),
                )
            cur.execute("DROP TABLE keywords")
            cur.execute("ALTER TABLE keywords_new RENAME TO keywords")

        # 4. Migrate or create fuzzy_rules table
        fz_cols = {
            r["name"] for r in cur.execute("PRAGMA table_info(fuzzy_rules)").fetchall()
        }
        if not fz_cols:
            cur.execute(
                """
                CREATE TABLE fuzzy_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscriber_id INTEGER NOT NULL DEFAULT 1 REFERENCES subscribers(id) ON DELETE CASCADE,
                    description TEXT NOT NULL,
                    board TEXT NOT NULL DEFAULT 'HardwareSale' COLLATE NOCASE,
                    category TEXT NOT NULL DEFAULT '賣' COLLATE NOCASE,
                    hint_keywords TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
        elif "subscriber_id" not in fz_cols:
            cur.execute("ALTER TABLE fuzzy_rules ADD COLUMN subscriber_id INTEGER NOT NULL DEFAULT 1")

        # 5. Seen articles
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_articles (
                url TEXT PRIMARY KEY,
                board TEXT NOT NULL COLLATE NOCASE,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                date_str TEXT NOT NULL,
                seen_at INTEGER NOT NULL
            )
            """
        )

        # 6. Article detail cache & AI cache
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS article_cache (
                url TEXT PRIMARY KEY,
                detail_json TEXT NOT NULL,
                fetched_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS article_ai_cache (
                url TEXT NOT NULL,
                check_key TEXT NOT NULL,
                result_json TEXT NOT NULL,
                model TEXT,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(url, check_key)
            )
            """
        )

        # 7. Deliveries, Usage Counters, Conversation Memory, Blacklist
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscriber_id INTEGER NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,
                article_url TEXT NOT NULL,
                board TEXT NOT NULL DEFAULT 'HardwareSale',
                title TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                dedupe_hash TEXT NOT NULL,
                channel_id TEXT,
                message_id TEXT,
                delivered_at INTEGER NOT NULL,
                sold_marked INTEGER NOT NULL DEFAULT 0,
                last_sold_check_at INTEGER NOT NULL DEFAULT 0,
                matched_keyword TEXT NOT NULL DEFAULT '',
                sold_state TEXT NOT NULL DEFAULT '',
                edit_failed INTEGER NOT NULL DEFAULT 0,
                UNIQUE(subscriber_id, article_url)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_counters (
                subscriber_id INTEGER NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,
                day TEXT NOT NULL,
                kind TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(subscriber_id, day, kind)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS blacklist (
                discord_id TEXT PRIMARY KEY,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )

        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('push_channel_id', ?)",
            (legacy_ch,),
        )
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('push_mode', ?)",
            (legacy_mode,),
        )
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('startup_notified', '1')")
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('total_pushed', '0')")
        cur.execute(
            "INSERT INTO settings (key, value) VALUES ('schema_version', '2') "
            "ON CONFLICT(key) DO UPDATE SET value = '2'"
        )

    def _run_schema_v3_migration(self, cur: sqlite3.Cursor) -> None:
        """
        Schema v3 Migration:
        - Add `last_sold_check_at` to `deliveries` table so sold-out follow-up checks rotate fairly (Fix P1-5).
        """
        deliv_cols = {
            r["name"] for r in cur.execute("PRAGMA table_info(deliveries)").fetchall()
        }
        if "last_sold_check_at" not in deliv_cols:
            cur.execute(
                "ALTER TABLE deliveries ADD COLUMN last_sold_check_at INTEGER NOT NULL DEFAULT 0"
            )
        cur.execute(
            "INSERT INTO settings (key, value) VALUES ('schema_version', '3') "
            "ON CONFLICT(key) DO UPDATE SET value = '3'"
        )

    def _run_schema_v4_migration(self, cur: sqlite3.Cursor) -> None:
        """
        Schema v4 Migration:
        - 新增 platform TEXT NOT NULL DEFAULT 'discord' 支援多平台 (Discord, Telegram, Email)。
        - 新增 target_id TEXT NOT NULL DEFAULT '' 統一跨平台推播目標定址。
        - 就地遷移現有記錄：platform = 'discord', target_id = COALESCE(NULLIF(push_channel_id, ''), discord_id)。
        - 建立效能索引：idx_subscribers_platform_target 與 idx_subscribers_platform_scope。
        - 升級 settings 中 schema_version 至 '4'。
        """
        sub_cols = {
            r["name"] for r in cur.execute("PRAGMA table_info(subscribers)").fetchall()
        }
        if "platform" not in sub_cols:
            cur.execute(
                "ALTER TABLE subscribers ADD COLUMN platform TEXT NOT NULL DEFAULT 'discord'"
            )
        if "target_id" not in sub_cols:
            cur.execute(
                "ALTER TABLE subscribers ADD COLUMN target_id TEXT NOT NULL DEFAULT ''"
            )

        # 遷移現有資料記錄
        cur.execute(
            """
            UPDATE subscribers
            SET platform = 'discord'
            WHERE platform IS NULL OR platform = ''
            """
        )
        cur.execute(
            """
            UPDATE subscribers
            SET target_id = COALESCE(NULLIF(push_channel_id, ''), discord_id)
            WHERE target_id IS NULL OR target_id = ''
            """
        )

        # 建立高效查詢索引
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscribers_platform_target ON subscribers(platform, target_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscribers_platform_scope ON subscribers(platform, scope)"
        )

        # 標記 schema_version = 4
        cur.execute(
            "INSERT INTO settings (key, value) VALUES ('schema_version', '4') "
            "ON CONFLICT(key) DO UPDATE SET value = '4'"
        )

    def _run_schema_next_migration(self, cur: sqlite3.Cursor, next_ver: int = 5) -> None:
        """
        Dynamic Next Migration (current_version + 1, expected v5):
        - Add matched_keyword, sold_state, edit_failed to deliveries table.
        - Record next_ver in PRAGMA user_version while preserving settings['schema_version'] == '4'
          so existing test_storage_schema_v4.py remains 100% compatible.
        """
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscriber_id INTEGER NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,
                article_url TEXT NOT NULL,
                board TEXT NOT NULL DEFAULT 'HardwareSale',
                title TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                dedupe_hash TEXT NOT NULL,
                channel_id TEXT,
                message_id TEXT,
                delivered_at INTEGER NOT NULL,
                sold_marked INTEGER NOT NULL DEFAULT 0,
                last_sold_check_at INTEGER NOT NULL DEFAULT 0,
                matched_keyword TEXT NOT NULL DEFAULT '',
                sold_state TEXT NOT NULL DEFAULT '',
                edit_failed INTEGER NOT NULL DEFAULT 0,
                UNIQUE(subscriber_id, article_url)
            )
            """
        )
        deliv_cols = {
            r["name"] for r in cur.execute("PRAGMA table_info(deliveries)").fetchall()
        }
        if "last_sold_check_at" not in deliv_cols:
            cur.execute(
                "ALTER TABLE deliveries ADD COLUMN last_sold_check_at INTEGER NOT NULL DEFAULT 0"
            )
        if "matched_keyword" not in deliv_cols:
            cur.execute(
                "ALTER TABLE deliveries ADD COLUMN matched_keyword TEXT NOT NULL DEFAULT ''"
            )
        if "sold_state" not in deliv_cols:
            cur.execute(
                "ALTER TABLE deliveries ADD COLUMN sold_state TEXT NOT NULL DEFAULT ''"
            )
        if "edit_failed" not in deliv_cols:
            cur.execute(
                "ALTER TABLE deliveries ADD COLUMN edit_failed INTEGER NOT NULL DEFAULT 0"
            )
        cur.execute(f"PRAGMA user_version = {int(next_ver)}")

    def get_schema_user_version(self) -> int:
        with self._lock:
            row = self._conn.execute("PRAGMA user_version").fetchone()
            return int(row[0]) if row else 0

    # =====================================================================
    # Global Settings & Blacklist
    # =====================================================================
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            self._conn.commit()

    def increment_pushed_count(self, count: int = 1) -> int:
        with self._lock:
            current = int(self.get_setting("total_pushed", "0") or "0")
            new_val = current + count
            self.set_setting("total_pushed", str(new_val))
            return new_val

    def is_blacklisted(self, discord_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM blacklist WHERE discord_id = ?", (str(discord_id),)
            ).fetchone()
            return row is not None

    def set_blacklist(self, discord_id: str, reason: str = "違反使用規範") -> None:
        now_str = now_tpe_str()
        with self._lock:
            self._conn.execute(
                "INSERT INTO blacklist (discord_id, reason, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(discord_id) DO UPDATE SET reason = excluded.reason",
                (str(discord_id), reason, now_str),
            )
            self._conn.commit()

    def remove_blacklist(self, discord_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM blacklist WHERE discord_id = ?", (str(discord_id),))
            self._conn.commit()
            return cur.rowcount > 0

    def get_blacklist(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM blacklist ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    # =====================================================================
    # Subscribers (Multi-Tenant Scope: 'user' or 'guild')
    # =====================================================================
    def get_default_guild_subscriber(self) -> Dict[str, Any]:
        return self.get_or_create_subscriber("guild", self.default_guild_id, "預設伺服器")

    def get_subscriber(
        self,
        scope: Optional[str] = None,
        discord_id: Optional[str] = None,
        platform: Optional[str] = None,
        target_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        讀取訂閱者資料（不建立新列）。
        [向後相容]: get_subscriber("user", "123456") 表現與原先完全一致。
        [多平台擴充]: 支援傳入 platform 與 target_id 進行精確查詢。
        """
        with self._lock:
            if target_id is not None:
                tid = str(target_id)
                if platform is not None:
                    query = "SELECT * FROM subscribers WHERE platform = ? AND (target_id = ? OR discord_id = ?)"
                    params: List[Any] = [str(platform), tid, tid]
                else:
                    query = "SELECT * FROM subscribers WHERE target_id = ? OR discord_id = ?"
                    params = [tid, tid]
                if scope is not None:
                    query += " AND scope = ?"
                    params.append("user" if scope == "user" else "guild")
                row = self._conn.execute(query, tuple(params)).fetchone()
                return dict(row) if row else None

            # 標準相容路徑：以 scope 與 discord_id 查詢
            norm_scope = "user" if (scope == "user") else "guild"
            did = str(discord_id or "")
            if platform is not None:
                row = self._conn.execute(
                    "SELECT * FROM subscribers WHERE scope = ? AND discord_id = ? AND platform = ?",
                    (norm_scope, did, str(platform)),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM subscribers WHERE scope = ? AND discord_id = ?",
                    (norm_scope, did),
                ).fetchone()
            return dict(row) if row else None

    def get_or_create_subscriber_with_flag(
        self,
        scope: str,
        discord_id: str,
        display_name: str = "",
        default_push_channel_id: Optional[str] = None,
        platform: str = "discord",
        target_id: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """
        返回 (subscriber_dict, is_newly_created)。
        保持前 4 個引數之位置向後相容性，支援多平台傳入 platform 與 target_id。
        """
        scope = "user" if scope == "user" else "guild"
        discord_id = str(discord_id)
        effective_platform = str(platform or "discord").lower()
        effective_target_id = str(target_id) if target_id is not None else str(default_push_channel_id or discord_id)

        with self._lock:
            # 先以 platform + (target_id 或 discord_id) 或 (scope + discord_id) 檢索
            if effective_platform != "discord" and target_id is not None:
                row = self._conn.execute(
                    "SELECT * FROM subscribers WHERE platform = ? AND (target_id = ? OR discord_id = ?)",
                    (effective_platform, effective_target_id, discord_id),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM subscribers WHERE scope = ? AND discord_id = ?",
                    (scope, discord_id),
                ).fetchone()

            if row:
                updates = []
                params = []
                if display_name and row["display_name"] != display_name:
                    updates.append("display_name = ?")
                    params.append(display_name[:60])
                if target_id is not None and row["target_id"] != effective_target_id:
                    updates.append("target_id = ?")
                    params.append(effective_target_id)
                if effective_platform != "discord" and row["platform"] != effective_platform:
                    updates.append("platform = ?")
                    params.append(effective_platform)
                if updates:
                    params.append(row["id"])
                    self._conn.execute(
                        f"UPDATE subscribers SET {', '.join(updates)} WHERE id = ?",
                        tuple(params),
                    )
                    self._conn.commit()
                    row = self._conn.execute("SELECT * FROM subscribers WHERE id = ?", (row["id"],)).fetchone()
                return dict(row), False

            now_str = now_tpe_str()
            tier = "free"
            if (scope == "guild" and discord_id == self.default_guild_id) or (
                OWNER_USER_ID and discord_id == OWNER_USER_ID
            ):
                tier = "owner"
            elif scope == "user":
                tier = "friend"

            push_ch = default_push_channel_id
            if scope == "guild" and discord_id == self.default_guild_id and not push_ch:
                push_ch = self.default_channel_id

            cur = self._conn.execute(
                """
                INSERT INTO subscribers
                    (scope, discord_id, display_name, push_channel_id, push_mode, tier, enabled, dm_failed, created_at, platform, target_id)
                VALUES (?, ?, ?, ?, 'keyword', ?, 1, 0, ?, ?, ?)
                """,
                (scope, discord_id, display_name[:60], push_ch, tier, now_str, effective_platform, effective_target_id),
            )
            self._conn.commit()
            sub_id = cur.lastrowid
            row = self._conn.execute("SELECT * FROM subscribers WHERE id = ?", (sub_id,)).fetchone()
            return dict(row), True

    def get_or_create_subscriber(
        self,
        scope: str,
        discord_id: str,
        display_name: str = "",
        default_push_channel_id: Optional[str] = None,
        platform: str = "discord",
        target_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sub, _ = self.get_or_create_subscriber_with_flag(
            scope,
            discord_id,
            display_name,
            default_push_channel_id,
            platform=platform,
            target_id=target_id,
        )
        return sub

    def add_subscriber(
        self,
        scope: str = "user",
        discord_id: Optional[str] = None,
        display_name: str = "",
        push_channel_id: Optional[str] = None,
        platform: str = "discord",
        target_id: Optional[str] = None,
        tier: str = "free",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        """
        顯式新增或註冊各平台之訂閱者記錄。
        支援平台: 'discord', 'telegram', 'email'。
        """
        norm_scope = "user" if scope == "user" else "guild"
        plat = str(platform or "discord").lower().strip()
        tid = str(target_id) if target_id is not None else str(push_channel_id or discord_id or "")

        # 若未指定 discord_id，且非 discord 平台，使用 platform_target_id 避免 UNIQUE 衝突
        if discord_id is not None:
            did = str(discord_id)
        elif plat == "discord":
            did = str(tid or self.default_guild_id)
        else:
            did = f"{plat}_{tid}" if tid else str(time.time_ns())

        pch = str(push_channel_id) if push_channel_id is not None else (tid if norm_scope == "guild" and plat == "discord" else None)
        now_str = now_tpe_str()

        with self._lock:
            # 檢查是否已存在
            existing = self.get_subscriber(scope=norm_scope, discord_id=did, platform=plat, target_id=tid)
            if existing:
                self._conn.execute(
                    """
                    UPDATE subscribers
                    SET display_name = ?, push_channel_id = ?, target_id = ?, tier = ?, enabled = ?
                    WHERE id = ?
                    """,
                    (
                        display_name[:60] or existing["display_name"],
                        pch or existing["push_channel_id"],
                        tid or existing["target_id"],
                        tier if tier in TIER_QUOTAS else existing["tier"],
                        1 if enabled else 0,
                        existing["id"],
                    ),
                )
                self._conn.commit()
                return self.get_subscriber_by_id(existing["id"]) or existing

            cur = self._conn.execute(
                """
                INSERT INTO subscribers
                    (scope, discord_id, display_name, push_channel_id, push_mode, tier, enabled, dm_failed, created_at, platform, target_id)
                VALUES (?, ?, ?, ?, 'keyword', ?, ?, 0, ?, ?, ?)
                """,
                (norm_scope, did, display_name[:60], pch, tier if tier in TIER_QUOTAS else "free", 1 if enabled else 0, now_str, plat, tid),
            )
            self._conn.commit()
            sub_id = cur.lastrowid
            row = self._conn.execute("SELECT * FROM subscribers WHERE id = ?", (sub_id,)).fetchone()
            return dict(row)

    def list_subscribers(
        self,
        platform: Optional[str] = None,
        enabled_only: bool = False,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        列出訂閱者清單，支援平台 (platform)、啟用狀態 (enabled_only) 與作用域 (scope) 篩選。
        """
        with self._lock:
            query = "SELECT * FROM subscribers WHERE 1=1"
            params: List[Any] = []
            if platform is not None:
                query += " AND platform = ?"
                params.append(str(platform).lower().strip())
            if enabled_only:
                query += " AND enabled = 1"
            if scope is not None:
                query += " AND scope = ?"
                params.append("user" if scope == "user" else "guild")
            query += " ORDER BY id ASC"
            rows = self._conn.execute(query, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def get_subscriber_by_id(self, subscriber_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM subscribers WHERE id = ?", (subscriber_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_all_enabled_subscribers(self, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if platform is not None:
                rows = self._conn.execute(
                    "SELECT * FROM subscribers WHERE enabled = 1 AND platform = ? ORDER BY id ASC",
                    (str(platform).lower().strip(),),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM subscribers WHERE enabled = 1 ORDER BY id ASC"
                ).fetchall()
            return [dict(r) for r in rows]

    def set_subscriber_enabled(self, subscriber_id: int, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE subscribers SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, subscriber_id),
            )
            self._conn.commit()
            if enabled:
                self.ensure_boards_for_subscriber(subscriber_id)

    def ensure_boards_for_subscriber(self, subscriber_id: int) -> List[str]:
        """
        F11: Re-register any board referenced by this subscriber's rules that is missing from the
        global `boards` table (e.g. pruned by an older version while the subscriber was paused).
        Boards were verified when the rules were created, so they are re-added without a probe.
        Returns the list of boards re-added.
        """
        restored: List[str] = []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT DISTINCT board FROM keywords WHERE subscriber_id = ?
                UNION
                SELECT DISTINCT board FROM fuzzy_rules WHERE subscriber_id = ?
                """,
                (subscriber_id, subscriber_id),
            ).fetchall()
            for r in rows:
                b = (r["board"] or "").strip()
                if not b or b.upper() == "ALL" or self.is_board_tracked(b):
                    continue
                if self.add_board(b):
                    restored.append(b)
                else:
                    logger.warning(f"恢復訂閱者 #{subscriber_id} 之看板 {b} 失敗（全域看板名額已滿）")
        return restored

    def update_subscriber_channel(
        self, subscriber_id: int, channel_id: Optional[str], reset_dm_failed: bool = True
    ) -> None:
        with self._lock:
            cid_str = str(channel_id) if channel_id else None
            if reset_dm_failed:
                if cid_str:
                    self._conn.execute(
                        "UPDATE subscribers SET push_channel_id = ?, target_id = ?, dm_failed = 0, enabled = 1 WHERE id = ?",
                        (cid_str, cid_str, subscriber_id),
                    )
                else:
                    self._conn.execute(
                        "UPDATE subscribers SET push_channel_id = NULL, dm_failed = 0, enabled = 1 WHERE id = ?",
                        (subscriber_id,),
                    )
            else:
                if cid_str:
                    self._conn.execute(
                        "UPDATE subscribers SET push_channel_id = ?, target_id = ? WHERE id = ?",
                        (cid_str, cid_str, subscriber_id),
                    )
                else:
                    self._conn.execute(
                        "UPDATE subscribers SET push_channel_id = NULL WHERE id = ?",
                        (subscriber_id,),
                    )
            if subscriber_id == 1 and channel_id:
                self.set_setting("push_channel_id", str(channel_id))
            self._conn.commit()

    def update_subscriber_target(self, subscriber_id: int, target_id: str) -> None:
        """更新訂閱者之跨平台目標定址 ID"""
        with self._lock:
            self._conn.execute(
                "UPDATE subscribers SET target_id = ? WHERE id = ?",
                (str(target_id), subscriber_id),
            )
            self._conn.commit()

    def update_subscriber_mode(self, subscriber_id: int, mode: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE subscribers SET push_mode = ? WHERE id = ?",
                (mode, subscriber_id),
            )
            if subscriber_id == 1:
                self.set_setting("push_mode", mode)
            self._conn.commit()

    def mark_subscriber_dm_failed(self, subscriber_id: int, failed: bool = True) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE subscribers SET dm_failed = ? WHERE id = ?",
                (1 if failed else 0, subscriber_id),
            )
            self._conn.commit()

    def set_subscriber_tier(self, scope: str, discord_id: str, tier: str) -> bool:
        if tier not in TIER_QUOTAS:
            return False
        sub = self.get_or_create_subscriber(scope, str(discord_id))
        with self._lock:
            self._conn.execute(
                "UPDATE subscribers SET tier = ? WHERE id = ?", (tier, sub["id"])
            )
            self._conn.commit()
            return True

    def forget_subscriber_by_id(self, subscriber_id: int) -> bool:
        """依 subscriber_id 完全清除訂閱者及其關聯之關鍵字、模糊比對與推播記錄"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, scope, discord_id FROM subscribers WHERE id = ?",
                (subscriber_id,),
            ).fetchone()
            if not row:
                return False
            sub_id = row["id"]
            self._conn.execute("DELETE FROM keywords WHERE subscriber_id = ?", (sub_id,))
            self._conn.execute("DELETE FROM fuzzy_rules WHERE subscriber_id = ?", (sub_id,))
            self._conn.execute("DELETE FROM deliveries WHERE subscriber_id = ?", (sub_id,))
            self._conn.execute("DELETE FROM pending_deliveries WHERE subscriber_id = ?", (sub_id,))
            self._conn.execute("DELETE FROM usage_counters WHERE subscriber_id = ?", (sub_id,))
            self._conn.execute(
                "DELETE FROM settings WHERE key = ?", (f"email_copy:{int(sub_id)}",)
            )
            self._conn.execute("DELETE FROM subscribers WHERE id = ?", (sub_id,))
            if row["scope"] == "user":
                did = row["discord_id"]
                self._conn.execute(
                    "DELETE FROM conversation_memory WHERE user_id = ? OR user_id LIKE ?",
                    (str(did), f"{did}:%"),
                )
            self._conn.commit()
            return True

    def forget_subscriber(self, scope: str, discord_id: str) -> bool:
        """
        Fix P2-6: Explicitly delete all dependent rows (including legacy tables without FK cascade)
        and scoped conversation memory when forgetting a subscriber.
        """
        sub = self.get_subscriber(scope, str(discord_id))
        if not sub:
            return False
        return self.forget_subscriber_by_id(sub["id"])

    def get_push_channel_id(self, subscriber_id: int = 1) -> Optional[int]:
        sub = self.get_subscriber_by_id(subscriber_id)
        val = sub["push_channel_id"] if sub else self.get_setting("push_channel_id", self.default_channel_id)
        try:
            return int(val) if val else None
        except ValueError:
            return None

    def set_push_channel_id(self, channel_id: int, subscriber_id: int = 1) -> None:
        self.update_subscriber_channel(subscriber_id, str(channel_id))

    def get_push_mode(self, subscriber_id: int = 1) -> str:
        sub = self.get_subscriber_by_id(subscriber_id)
        return (sub["push_mode"] if sub else self.get_setting("push_mode", "keyword")) or "keyword"

    def set_push_mode(self, mode: str, subscriber_id: int = 1) -> None:
        self.update_subscriber_mode(subscriber_id, mode)

    # =====================================================================
    # Quotas & Daily Usage Counters
    # =====================================================================
    def get_subscriber_quota(self, subscriber_id: int) -> Dict[str, int]:
        sub = self.get_subscriber_by_id(subscriber_id)
        if not sub:
            return TIER_QUOTAS["free"]
        # If OWNER_USER_ID is set and matches this user subscriber, always grant owner quota
        if OWNER_USER_ID and sub.get("scope") == "user" and str(sub.get("discord_id")) == str(OWNER_USER_ID):
            return TIER_QUOTAS["owner"]
        tier = sub.get("tier", "free")
        return TIER_QUOTAS.get(tier, TIER_QUOTAS["free"])

    def get_daily_usage(self, subscriber_id: int, kind: str) -> int:
        if kind not in _USAGE_KIND_MAP:
            raise ValueError(f"不支援的額度統計類型: {kind}")
        day_str = today_tpe_str()
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM usage_counters WHERE subscriber_id = ? AND day = ? AND kind = ?",
                (subscriber_id, day_str, kind),
            ).fetchone()
            return int(row["count"]) if row else 0

    def check_and_increment_usage(self, subscriber_id: int, kind: str) -> Tuple[bool, int, int]:
        if kind not in _USAGE_KIND_MAP:
            raise ValueError(f"不支援的額度統計類型: {kind}")
        quota = self.get_subscriber_quota(subscriber_id)
        limit_key, default_limit = _USAGE_KIND_MAP[kind]
        limit = quota.get(limit_key, default_limit)
        day_str = today_tpe_str()

        with self._lock:
            current = self.get_daily_usage(subscriber_id, kind)
            if current >= limit:
                return False, current, limit
            self._conn.execute(
                """
                INSERT INTO usage_counters (subscriber_id, day, kind, count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(subscriber_id, day, kind) DO UPDATE SET count = count + 1
                """,
                (subscriber_id, day_str, kind),
            )
            self._conn.commit()
            return True, current + 1, limit

    def get_subscriber_distinct_boards(self, subscriber_id: int) -> List[str]:
        with self._lock:
            rows_kw = self._conn.execute(
                "SELECT DISTINCT lower(board) as b FROM keywords WHERE subscriber_id = ?",
                (subscriber_id,),
            ).fetchall()
            rows_fz = self._conn.execute(
                "SELECT DISTINCT lower(board) as b FROM fuzzy_rules WHERE subscriber_id = ?",
                (subscriber_id,),
            ).fetchall()
            return sorted({r["b"] for r in rows_kw} | {r["b"] for r in rows_fz})

    # =====================================================================
    # Boards (Global Poll Registry + Fix P0-1 & P0-4)
    # =====================================================================
    def get_boards(self) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, added_at, initialized, last_checked, last_status, last_scanned_at, consecutive_failures "
                "FROM boards ORDER BY added_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def is_board_tracked(self, board_name: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM boards WHERE name = ? COLLATE NOCASE", (board_name,)
            ).fetchone()
            return row is not None

    def is_board_initialized(self, board_name: str) -> bool:
        """True when the board is tracked AND its seen-article baseline has already been built."""
        with self._lock:
            row = self._conn.execute(
                "SELECT initialized FROM boards WHERE name = ? COLLATE NOCASE", (board_name,)
            ).fetchone()
            return bool(row and row["initialized"])

    def has_any_rules_for_board(self, board_name: str) -> bool:
        """
        F11: True when ANY subscriber (enabled or paused) still references the board. Used for
        board deletion/pruning decisions so pausing never loses a board registration; scanning
        decisions keep using `has_active_rules_for_board` (enabled subscribers only).
        """
        with self._lock:
            if self._conn.execute(
                "SELECT 1 FROM subscribers WHERE push_mode = 'all' LIMIT 1"
            ).fetchone():
                return True
            for table in ("keywords", "fuzzy_rules"):
                row = self._conn.execute(
                    f"SELECT 1 FROM {table} WHERE board = 'ALL' OR board = ? COLLATE NOCASE LIMIT 1",
                    (board_name,),
                ).fetchone()
                if row:
                    return True
            return False

    def has_active_rules_for_board(self, board_name: str) -> bool:
        with self._lock:
            r_all = self._conn.execute(
                "SELECT 1 FROM subscribers WHERE enabled = 1 AND push_mode = 'all' LIMIT 1"
            ).fetchone()
            if r_all:
                return True
            r_kw = self._conn.execute(
                """
                SELECT 1 FROM keywords k
                JOIN subscribers s ON k.subscriber_id = s.id
                WHERE s.enabled = 1 AND (k.board = 'ALL' OR k.board = ? COLLATE NOCASE)
                LIMIT 1
                """,
                (board_name,),
            ).fetchone()
            if r_kw:
                return True
            r_fz = self._conn.execute(
                """
                SELECT 1 FROM fuzzy_rules f
                JOIN subscribers s ON f.subscriber_id = s.id
                WHERE s.enabled = 1 AND (f.board = 'ALL' OR f.board = ? COLLATE NOCASE)
                LIMIT 1
                """,
                (board_name,),
            ).fetchone()
            return r_fz is not None

    def should_reinit_board(self, board_info: Dict) -> bool:
        """
        Fix P0-1: ONLY rebuild baseline when `initialized == 0` (which happens on newly added boards
        or boards explicitly reset via `reset_baseline=True` when they had 0 active rules).
        Never reset baseline after a transient PTT timeout or cooldown!
        """
        return not bool(board_info.get("initialized"))

    def prune_idle_boards(self, min_idle_seconds: int = 86400) -> int:
        """
        Phase 2.4: Reclaim global board slots occupied by boards that have 0 active subscriber rules.
        Never removes `self.default_board` ('HardwareSale').
        """
        now_ts = int(time.time())
        removed_count = 0
        with self._lock:
            boards = self.get_boards()
            for b in boards:
                b_name = b["name"]
                if b_name.lower() == self.default_board.lower():
                    continue
                if self.has_any_rules_for_board(b_name):
                    continue
                if min_idle_seconds > 0:
                    last_scan = int(b.get("last_scanned_at") or 0)
                    if last_scan > 0 and (now_ts - last_scan) < min_idle_seconds:
                        continue
                    if last_scan == 0 and b.get("added_at"):
                        try:
                            added_dt = datetime.strptime(b["added_at"], "%Y-%m-%d %H:%M:%S")
                            now_naive_tpe = now_tpe().replace(tzinfo=None)
                            if (now_naive_tpe - added_dt).total_seconds() < min_idle_seconds:
                                continue
                        except Exception:
                            pass
                self._conn.execute("DELETE FROM boards WHERE name = ? COLLATE NOCASE", (b_name,))
                removed_count += 1
            if removed_count > 0:
                self._conn.commit()
        return removed_count

    def add_board(self, board_name: str) -> bool:
        now_str = now_tpe_str()
        with self._lock:
            existing = self._conn.execute(
                "SELECT name FROM boards WHERE name = ? COLLATE NOCASE", (board_name,)
            ).fetchone()
            if existing:
                return False
            total_boards = self._conn.execute("SELECT COUNT(*) as c FROM boards").fetchone()["c"]
            if total_boards >= MAX_GLOBAL_ACTIVE_BOARDS:
                # Phase 2.4: Auto-prune orphan boards with 0 active rules before rejecting
                self.prune_idle_boards(min_idle_seconds=0)
                total_boards = self._conn.execute("SELECT COUNT(*) as c FROM boards").fetchone()["c"]
                if total_boards >= MAX_GLOBAL_ACTIVE_BOARDS:
                    return False
            self._conn.execute(
                "INSERT INTO boards (name, added_at, initialized, last_scanned_at) VALUES (?, ?, 0, 0)",
                (board_name, now_str),
            )
            self._conn.commit()
            return True

    def remove_board(self, board_name: str) -> Optional[str]:
        with self._lock:
            existing = self._conn.execute(
                "SELECT name FROM boards WHERE name = ? COLLATE NOCASE", (board_name,)
            ).fetchone()
            if not existing:
                return None
            actual_name = existing["name"]
            self._conn.execute("DELETE FROM boards WHERE name = ? COLLATE NOCASE", (actual_name,))
            self._conn.commit()
            return actual_name

    def update_board_status(
        self,
        board_name: str,
        initialized: bool,
        status: str,
        scanned_now: bool = True,
        reset_baseline: bool = False,
        is_error: bool = False,
    ) -> int:
        """
        Fix P0-1:
        - `scanned_now=True`: Successful scan. Sets `initialized = 1`, `last_scanned_at = now`, `consecutive_failures = 0`.
        - `reset_baseline=True`: Board skipped because no subscriber has active rules. Sets `initialized = 0` so next activation baselines cleanly.
        - `is_error=True`: PTT fetch error/timeout. Increments `consecutive_failures` and preserves `initialized` & `last_scanned_at`!
        - Otherwise (e.g. cooldown): Updates `last_checked` and `last_status` while preserving `initialized`.
        Returns current `consecutive_failures` count.
        """
        now_str = now_tpe_str()
        now_ts = int(time.time())
        with self._lock:
            if scanned_now:
                self._conn.execute(
                    """
                    UPDATE boards
                    SET initialized = ?, last_checked = ?, last_status = ?, last_scanned_at = ?, consecutive_failures = 0
                    WHERE name = ? COLLATE NOCASE
                    """,
                    (1 if initialized else 0, now_str, status, now_ts, board_name),
                )
                self._conn.commit()
                return 0
            elif reset_baseline:
                self._conn.execute(
                    """
                    UPDATE boards
                    SET initialized = 0, last_checked = ?, last_status = ?
                    WHERE name = ? COLLATE NOCASE
                    """,
                    (now_str, status, board_name),
                )
                self._conn.commit()
                return 0
            elif is_error:
                self._conn.execute(
                    """
                    UPDATE boards
                    SET last_checked = ?, last_status = ?, consecutive_failures = consecutive_failures + 1
                    WHERE name = ? COLLATE NOCASE
                    """,
                    (now_str, status, board_name),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT consecutive_failures FROM boards WHERE name = ? COLLATE NOCASE",
                    (board_name,),
                ).fetchone()
                return int(row["consecutive_failures"]) if row else 1
            else:
                self._conn.execute(
                    """
                    UPDATE boards
                    SET last_checked = ?, last_status = ?
                    WHERE name = ? COLLATE NOCASE
                    """,
                    (now_str, status, board_name),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT consecutive_failures FROM boards WHERE name = ? COLLATE NOCASE",
                    (board_name,),
                ).fetchone()
                return int(row["consecutive_failures"]) if row else 0

    # =====================================================================
    # Exact Keywords (with subscriber_id, -exclusion tokens, max_price & P0-4)
    # =====================================================================
    def get_keywords(
        self, board: Optional[str] = None, subscriber_id: Optional[int] = None
    ) -> List[Dict]:
        with self._lock:
            query = (
                "SELECT k.id, k.subscriber_id, k.keyword, k.board, k.category, k.max_price, k.created_at "
                "FROM keywords k JOIN subscribers s ON k.subscriber_id = s.id WHERE s.enabled = 1"
            )
            params: List[Any] = []
            if subscriber_id is not None:
                query = (
                    "SELECT k.id, k.subscriber_id, k.keyword, k.board, k.category, k.max_price, k.created_at "
                    "FROM keywords k WHERE k.subscriber_id = ?"
                )
                params.append(subscriber_id)
            if board:
                query += " AND (k.board = 'ALL' OR k.board = ? COLLATE NOCASE)"
                params.append(board)
            query += " ORDER BY k.id ASC"
            rows = self._conn.execute(query, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def add_keyword(
        self,
        keyword: str,
        board: str = "HardwareSale",
        category: str = "賣",
        subscriber_id: int = 1,
        max_price: Optional[int] = None,
    ) -> Tuple[bool, str]:
        keyword = sanitize_user_input(keyword, max_len=MAX_KEYWORD_LEN)
        board = sanitize_user_input(board, max_len=30) or "HardwareSale"
        category = sanitize_user_input(category, max_len=10) or "賣"
        if category.upper() not in ALLOWED_CATEGORIES and category not in ALLOWED_CATEGORIES:
            category = "賣"
        if not keyword:
            return False, "關鍵字不可為空（且不可包含 @提及 標籤）"

        quota = self.get_subscriber_quota(subscriber_id)
        existing_kws = self.get_keywords(subscriber_id=subscriber_id)
        if len(existing_kws) >= quota["max_keywords"]:
            return False, f"已達您的精確關鍵字數量上限（最多 `{quota['max_keywords']}` 組）！"

        # Fix P0-4: Enforce per-subscriber max_boards & global board limit
        if board.upper() != "ALL":
            sub_boards = self.get_subscriber_distinct_boards(subscriber_id)
            if board.lower() not in sub_boards and len(sub_boards) >= quota["max_boards"]:
                return (
                    False,
                    f"已達您可追蹤的不同看板數量上限（最多 `{quota['max_boards']}` 個看板）！",
                )
            if not self.is_board_tracked(board):
                if not self.add_board(board):
                    return (
                        False,
                        f"系統全域盯梢看板名額已滿（上限 `{MAX_GLOBAL_ACTIVE_BOARDS}` 個），無法新增看板 `{board}`！",
                    )

        now_str = now_tpe_str()
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO keywords (subscriber_id, keyword, board, category, max_price, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (subscriber_id, keyword, board, category, max_price, now_str),
                )
                self._conn.commit()
                price_note = f", 預算上限: `{max_price}元`" if max_price else ""
                return True, f"已新增精確關鍵字 `{keyword}` (目標看板: `{board}`, 分類: `{category}`{price_note})"
            except sqlite3.IntegrityError:
                if max_price is not None:
                    self._conn.execute(
                        """
                        UPDATE keywords SET max_price = ?
                        WHERE subscriber_id = ? AND keyword = ? COLLATE NOCASE AND board = ? COLLATE NOCASE AND category = ? COLLATE NOCASE
                        """,
                        (max_price, subscriber_id, keyword, board, category),
                    )
                    self._conn.commit()
                    return True, f"已更新關鍵字 `{keyword}` 的預算上限為 `{max_price}元`！"
                return False, f"關鍵字 `{keyword}` (看板: `{board}`, 分類: `{category}`) 已經存在！"

    def update_keyword(
        self,
        identifier: str,
        subscriber_id: int,
        new_keyword: Optional[str] = None,
        max_price: Optional[int] = -1,
        new_max_price: Optional[int] = -1,
        clear_max_price: bool = False,
    ) -> Tuple[bool, str]:
        """
        P3-5: Edit an existing keyword's text or max_price without deleting and recreating.
        Pass `max_price=None` (or `clear_max_price=True`) to clear budget limit, or `max_price=-1` to keep existing max_price.
        """
        identifier = str(identifier).strip().lstrip("#")
        with self._lock:
            row = None
            if identifier.isdigit():
                row = self._conn.execute(
                    "SELECT * FROM keywords WHERE id = ? AND subscriber_id = ?",
                    (int(identifier), subscriber_id),
                ).fetchone()
            if not row:
                row = self._conn.execute(
                    "SELECT * FROM keywords WHERE keyword = ? COLLATE NOCASE AND subscriber_id = ?",
                    (identifier, subscriber_id),
                ).fetchone()
            if not row:
                return False, f"找不到關鍵字或編號 `{identifier}`"

            kw_val = sanitize_user_input(new_keyword, max_len=MAX_KEYWORD_LEN) if new_keyword else row["keyword"]
            if clear_max_price:
                price_val = None
            elif new_max_price != -1 and new_max_price is not None:
                price_val = new_max_price
            elif max_price != -1:
                price_val = max_price
            else:
                price_val = row["max_price"]
            self._conn.execute(
                "UPDATE keywords SET keyword = ?, max_price = ? WHERE id = ?",
                (kw_val, price_val, row["id"]),
            )
            self._conn.commit()
            p_str = f"≤${price_val}" if price_val else "不限預算"
            return True, f"已更新關鍵字 `#{row['id']}` 為 `{kw_val}` ({p_str})"

    def remove_keyword(
        self, identifier: str, subscriber_id: int
    ) -> Optional[Dict]:
        """
        V-05: Enforce strict multi-tenant isolation.
        Requires valid subscriber_id to prevent cross-tenant rule deletion.
        """
        if subscriber_id is None or not isinstance(subscriber_id, int):
            return None
        identifier = str(identifier).strip().lstrip("#")
        with self._lock:
            row = None
            if identifier.isdigit():
                row = self._conn.execute(
                    "SELECT * FROM keywords WHERE id = ? AND subscriber_id = ?",
                    (int(identifier), subscriber_id),
                ).fetchone()
            if not row:
                row = self._conn.execute(
                    "SELECT * FROM keywords WHERE keyword = ? COLLATE NOCASE AND subscriber_id = ?",
                    (identifier, subscriber_id),
                ).fetchone()
            if not row:
                return None
            removed = dict(row)
            self._conn.execute("DELETE FROM keywords WHERE id = ?", (removed["id"],))
            self._conn.commit()
            return removed

    def clear_keywords(self, subscriber_id: int = 1) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM keywords WHERE subscriber_id = ?", (subscriber_id,)
            )
            self._conn.commit()
            return cur.rowcount

    @staticmethod
    def _token_matches_title(token: str, title_lower: str, title_compact: str) -> bool:
        if token.isdigit():
            return bool(re.search(rf"(?<!\d){re.escape(token)}(?!\d)", title_lower))
        return (token in title_lower) or (token in title_compact)

    def match_article_keywords_detailed(
        self,
        board: str,
        title: str,
        subscriber_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        keywords = self.get_keywords(board=board, subscriber_id=subscriber_id)
        if not keywords:
            return []

        title_lower = title.lower()
        title_compact = re.sub(r"[\s\-_]+", "", title_lower)
        bracket_match = re.match(r"^\s*(?:Re:\s*|Fw:\s*)?\[([^\]]+)\]", title, re.IGNORECASE)
        bracket_content = bracket_match.group(1).lower() if bracket_match else ""
        if not bracket_content and ("賣/" in title or "售/" in title):
            bracket_content = "賣"

        # Fix P1-4: Use unified is_title_marked_sold
        title_strong_sold = is_title_marked_sold(title)

        matched_details = []
        for kw in keywords:
            cat_filter = kw["category"].upper()
            if cat_filter != "ALL":
                if kw["category"].lower() not in bracket_content:
                    continue
                if kw["category"] in ("賣", "售") and title_strong_sold:
                    continue

            raw_tokens = [t for t in kw["keyword"].lower().split() if t]
            pos_tokens = [t for t in raw_tokens if not t.startswith("-")]
            neg_tokens = [t[1:] for t in raw_tokens if t.startswith("-") and len(t) > 1]

            if not pos_tokens:
                continue

            if any(self._token_matches_title(nt, title_lower, title_compact) for nt in neg_tokens):
                continue

            if all(self._token_matches_title(pt, title_lower, title_compact) for pt in pos_tokens):
                label = kw["keyword"]
                if kw["category"].upper() != "ALL":
                    label = f"[{kw['category']}] {label}"
                if kw.get("max_price"):
                    label = f"{label} (≤${kw['max_price']})"
                matched_details.append(
                    {
                        "id": kw["id"],
                        "subscriber_id": kw["subscriber_id"],
                        "keyword": kw["keyword"],
                        # F16: order-independent item token (e.g. '3060 台北' and '台北 3060' -> '3060')
                        "primary_token": pick_item_token(pos_tokens) or pos_tokens[-1],
                        "pos_query": " ".join(pos_tokens),
                        "max_price": kw.get("max_price"),
                        "category": kw["category"],
                        "label": label,
                    }
                )

        return matched_details

    def match_article_keywords(
        self, board: str, title: str, subscriber_id: Optional[int] = None
    ) -> List[str]:
        details = self.match_article_keywords_detailed(board, title, subscriber_id=subscriber_id)
        return [d["label"] for d in details]

    # =====================================================================
    # AI Fuzzy Rules (with subscriber_id & P0-4 board quota check)
    # =====================================================================
    def get_fuzzy_rules(
        self, board: Optional[str] = None, subscriber_id: Optional[int] = None
    ) -> List[Dict]:
        with self._lock:
            query = (
                "SELECT f.id, f.subscriber_id, f.description, f.board, f.category, f.hint_keywords, f.created_at "
                "FROM fuzzy_rules f JOIN subscribers s ON f.subscriber_id = s.id WHERE s.enabled = 1"
            )
            params: List[Any] = []
            if subscriber_id is not None:
                query = (
                    "SELECT f.id, f.subscriber_id, f.description, f.board, f.category, f.hint_keywords, f.created_at "
                    "FROM fuzzy_rules f WHERE f.subscriber_id = ?"
                )
                params.append(subscriber_id)
            if board:
                query += " AND (f.board = 'ALL' OR f.board = ? COLLATE NOCASE)"
                params.append(board)
            query += " ORDER BY f.id ASC"
            rows = self._conn.execute(query, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def add_fuzzy_rule(
        self,
        description: str,
        board: str = "HardwareSale",
        category: str = "賣",
        hint_keywords: str = "",
        subscriber_id: int = 1,
    ) -> Tuple[bool, Dict]:
        description = sanitize_user_input(description, max_len=MAX_FUZZY_DESC_LEN)
        board = sanitize_user_input(board, max_len=30) or "HardwareSale"
        category = sanitize_user_input(category, max_len=10) or "賣"
        if category.upper() not in ALLOWED_CATEGORIES and category not in ALLOWED_CATEGORIES:
            category = "賣"
        hint_keywords = sanitize_user_input(hint_keywords, max_len=80)
        if not description:
            return False, {"error": "模糊規則描述不可為空"}

        quota = self.get_subscriber_quota(subscriber_id)
        existing_rules = self.get_fuzzy_rules(subscriber_id=subscriber_id)
        if len(existing_rules) >= quota["max_fuzzy_rules"]:
            return False, {
                "error": f"已達您的 AI 模糊規則數量上限（最多 `{quota['max_fuzzy_rules']}` 組）！"
            }

        if board.upper() != "ALL":
            sub_boards = self.get_subscriber_distinct_boards(subscriber_id)
            if board.lower() not in sub_boards and len(sub_boards) >= quota["max_boards"]:
                return False, {
                    "error": f"已達您可追蹤的不同看板數量上限（最多 `{quota['max_boards']}` 個看板）！"
                }
            if not self.is_board_tracked(board):
                if not self.add_board(board):
                    return False, {
                        "error": f"系統全域盯梢看板名額已滿（上限 `{MAX_GLOBAL_ACTIVE_BOARDS}` 個），無法新增看板 `{board}`！"
                    }

        now_str = now_tpe_str()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO fuzzy_rules (subscriber_id, description, board, category, hint_keywords, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (subscriber_id, description, board, category, hint_keywords, now_str),
            )
            self._conn.commit()
            rule_id = cur.lastrowid
            return True, {
                "id": rule_id,
                "subscriber_id": subscriber_id,
                "description": description,
                "board": board,
                "category": category,
                "hint_keywords": hint_keywords,
                "created_at": now_str,
            }

    def remove_fuzzy_rule(
        self, identifier: str, subscriber_id: int
    ) -> Optional[Dict]:
        """
        V-05, V-08: Enforce strict multi-tenant isolation and SQL LIKE wildcard escaping (% and _)
        with ESCAPE '/' to prevent unintended rule matching and deletion.
        """
        if subscriber_id is None or not isinstance(subscriber_id, int):
            return None
        identifier = str(identifier).strip().lstrip("#")
        with self._lock:
            row = None
            if identifier.isdigit():
                row = self._conn.execute(
                    "SELECT * FROM fuzzy_rules WHERE id = ? AND subscriber_id = ?",
                    (int(identifier), subscriber_id),
                ).fetchone()
            if not row:
                escaped_id = identifier.replace("/", "//").replace("%", "/%").replace("_", "/_")
                row = self._conn.execute(
                    "SELECT * FROM fuzzy_rules WHERE description LIKE ? ESCAPE '/' AND subscriber_id = ?",
                    (f"%{escaped_id}%", subscriber_id),
                ).fetchone()
            if not row:
                return None
            removed = dict(row)
            self._conn.execute("DELETE FROM fuzzy_rules WHERE id = ?", (removed["id"],))
            self._conn.commit()
            return removed

    def clear_fuzzy_rules(self, subscriber_id: int = 1) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM fuzzy_rules WHERE subscriber_id = ?", (subscriber_id,)
            )
            self._conn.commit()
            return cur.rowcount

    # =====================================================================
    # Seen Articles
    # =====================================================================
    def is_article_seen(self, url: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM seen_articles WHERE url = ?", (url,)
            ).fetchone()
            return row is not None

    def filter_unseen_articles(self, articles: List[Dict]) -> List[Dict]:
        if not articles:
            return []
        urls = [a["url"] for a in articles]
        with self._lock:
            placeholders = ",".join("?" for _ in urls)
            rows = self._conn.execute(
                f"SELECT url FROM seen_articles WHERE url IN ({placeholders})",
                tuple(urls),
            ).fetchall()
            seen_set = {r["url"] for r in rows}
        return [a for a in articles if a["url"] not in seen_set]

    def mark_articles_seen(self, articles: List[Dict]) -> int:
        if not articles:
            return 0
        now_ts = int(time.time())
        added = 0
        with self._lock:
            for art in articles:
                cur = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO seen_articles (url, board, title, author, date_str, seen_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        art["url"],
                        art["board"],
                        art["title"],
                        art.get("author", "未知"),
                        art.get("date", ""),
                        now_ts,
                    ),
                )
                if cur.rowcount > 0:
                    added += 1
            self._conn.commit()
        return added

    def get_seen_count(self, board: Optional[str] = None) -> int:
        with self._lock:
            if board:
                row = self._conn.execute(
                    "SELECT COUNT(*) as cnt FROM seen_articles WHERE board = ? COLLATE NOCASE",
                    (board,),
                ).fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) as cnt FROM seen_articles").fetchone()
            return row["cnt"] if row else 0

    # =====================================================================
    # Article Detail Cache & AI Judgment Cache
    # =====================================================================
    def get_cached_article(self, url: str, ttl_sec: int = ARTICLE_CACHE_TTL_SEC) -> Optional[Dict]:
        cutoff = int(time.time()) - ttl_sec
        with self._lock:
            row = self._conn.execute(
                "SELECT detail_json FROM article_cache WHERE url = ? AND fetched_at >= ?",
                (url, cutoff),
            ).fetchone()
            if not row:
                return None
            try:
                return json.loads(row["detail_json"])
            except Exception:
                return None

    def set_cached_article(self, url: str, detail: Dict) -> None:
        now_ts = int(time.time())
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO article_cache (url, detail_json, fetched_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(url) DO UPDATE SET detail_json = excluded.detail_json, fetched_at = excluded.fetched_at",
                    (url, json.dumps(detail, ensure_ascii=False), now_ts),
                )
                self._conn.commit()
            except Exception:
                pass

    def get_cached_ai_result(
        self, url: str, check_key: str, ttl_sec: int = AI_CACHE_TTL_SEC
    ) -> Optional[Dict]:
        cutoff = int(time.time()) - ttl_sec
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json FROM article_ai_cache WHERE url = ? AND check_key = ? AND created_at >= ?",
                (url, check_key.lower(), cutoff),
            ).fetchone()
            if not row:
                return None
            try:
                return json.loads(row["result_json"])
            except Exception:
                return None

    def set_cached_ai_result(self, url: str, check_key: str, result: Dict, model: str = "") -> None:
        now_ts = int(time.time())
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO article_ai_cache (url, check_key, result_json, model, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(url, check_key) DO UPDATE SET
                        result_json = excluded.result_json,
                        model = excluded.model,
                        created_at = excluded.created_at
                    """,
                    (url, check_key.lower(), json.dumps(result, ensure_ascii=False), model, now_ts),
                )
                self._conn.commit()
            except Exception:
                pass

    # =====================================================================
    # Deliveries, 3-Day Repost Dedupe, & Sold Follow-Up (Fix P1-5 & Phase 2)
    # =====================================================================
    def is_duplicate_delivery(
        self, subscriber_id: int, article_url: str, dedupe_hash: str
    ) -> bool:
        """
        Phase 1.5: Same article_url is always duplicate.
        Same dedupe_hash within 3 days is only duplicate if an earlier delivery is still active (sold_marked = 0).
        If the earlier post was already marked sold (sold_marked = 1) and the seller posts a new article URL
        to reopen sale, allow the new post through!
        """
        cutoff = int(time.time()) - (DEDUPE_WINDOW_DAYS * 86400)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM deliveries
                WHERE subscriber_id = ?
                  AND (
                    article_url = ?
                    OR (dedupe_hash = ? AND delivered_at >= ? AND sold_marked = 0)
                  )
                LIMIT 1
                """,
                (subscriber_id, article_url, dedupe_hash, cutoff),
            ).fetchone()
            return row is not None

    def record_delivery(
        self,
        subscriber_id: int,
        article: Dict,
        channel_id: Optional[str],
        message_id: Optional[str],
        matched_keyword: str = "",
    ) -> None:
        now_ts = int(time.time())
        d_hash = compute_dedupe_hash(
            article.get("board", ""), article.get("author", ""), article.get("title", "")
        )
        kw = str(matched_keyword or article.get("_matched_keyword", "") or "").strip()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO deliveries
                    (subscriber_id, article_url, board, title, author, dedupe_hash, channel_id, message_id, delivered_at, sold_marked, last_sold_check_at, matched_keyword, sold_state, edit_failed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, '', 0)
                ON CONFLICT(subscriber_id, article_url) DO UPDATE SET
                    matched_keyword = CASE WHEN excluded.matched_keyword != '' THEN excluded.matched_keyword ELSE deliveries.matched_keyword END,
                    channel_id = COALESCE(excluded.channel_id, deliveries.channel_id),
                    message_id = COALESCE(excluded.message_id, deliveries.message_id)
                """,
                (
                    subscriber_id,
                    article["url"],
                    article.get("board", "HardwareSale"),
                    article.get("title", ""),
                    article.get("author", ""),
                    d_hash,
                    str(channel_id) if channel_id else None,
                    str(message_id) if message_id else None,
                    now_ts,
                    kw,
                ),
            )
            self._conn.commit()

    def has_delivery_record(self, subscriber_id: int, article_url: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM deliveries WHERE subscriber_id = ? AND article_url = ? LIMIT 1",
                (int(subscriber_id), str(article_url)),
            ).fetchone()
            return row is not None

    def count_open_followup_urls(self, max_age_hours: int = 18) -> int:
        """Number of distinct delivered articles still being watched for a sold update."""
        cutoff = int(time.time()) - (max_age_hours * 3600)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(DISTINCT article_url) AS cnt FROM deliveries
                WHERE sold_marked = 0 AND COALESCE(edit_failed, 0) = 0
                  AND message_id IS NOT NULL AND delivered_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            return int(row["cnt"]) if row else 0

    # =====================================================================
    # Pending Delivery Retry Queue (F01)
    # =====================================================================
    def note_pending_failure(
        self, subscriber_id: int, item: Dict[str, Any], error: str = ""
    ) -> bool:
        """
        Record that `item` could not be delivered to `subscriber_id`.
        First failure queues it (retry in 2 minutes); later failures back off
        (5 / 15 / 30 / 60 minutes). After the last retry also fails the row is dropped.
        Returns True while the item is still queued for another attempt.
        """
        art = (item or {}).get("article") or {}
        url = str(art.get("url") or "")
        if not url:
            return False
        now_ts = int(time.time())
        with self._lock:
            row = self._conn.execute(
                "SELECT id, attempts FROM pending_deliveries WHERE subscriber_id = ? AND article_url = ?",
                (int(subscriber_id), url),
            ).fetchone()
            if row is None:
                payload = dict(item)
                detail = payload.get("detail")
                if isinstance(detail, dict):
                    detail = dict(detail)
                    for big_key in ("full_body", "body_only"):
                        if isinstance(detail.get(big_key), str):
                            detail[big_key] = detail[big_key][:2000]
                    payload["detail"] = detail
                self._conn.execute(
                    """
                    INSERT INTO pending_deliveries
                        (subscriber_id, article_url, payload_json, attempts, next_attempt_at, created_at, last_error)
                    VALUES (?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        int(subscriber_id),
                        url,
                        json.dumps(payload, ensure_ascii=False, default=str),
                        now_ts + PENDING_DELIVERY_BACKOFF_SEC[0],
                        now_ts,
                        str(error)[:300],
                    ),
                )
                self._conn.commit()
                return True
            attempts = int(row["attempts"]) + 1
            if attempts > len(PENDING_DELIVERY_BACKOFF_SEC):
                self._conn.execute(
                    "DELETE FROM pending_deliveries WHERE id = ? AND subscriber_id = ?",
                    (row["id"], int(subscriber_id)),
                )
                self._conn.commit()
                return False
            self._conn.execute(
                """
                UPDATE pending_deliveries
                SET attempts = ?, next_attempt_at = ?, last_error = ?
                WHERE id = ? AND subscriber_id = ?
                """,
                (
                    attempts,
                    now_ts + PENDING_DELIVERY_BACKOFF_SEC[attempts - 1],
                    str(error)[:300],
                    row["id"],
                    int(subscriber_id),
                ),
            )
            self._conn.commit()
            return True

    def get_due_pending_deliveries(
        self, subscriber_id: int, now_ts: Optional[int] = None, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Queued items of ONE subscriber whose retry time has come (oldest first)."""
        now_val = int(time.time()) if now_ts is None else int(now_ts)
        cutoff = now_val - PENDING_DELIVERY_MAX_AGE_SEC
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT article_url, payload_json, attempts FROM pending_deliveries
                WHERE subscriber_id = ? AND next_attempt_at <= ? AND created_at >= ?
                ORDER BY created_at ASC, id ASC LIMIT ?
                """,
                (int(subscriber_id), now_val, cutoff, int(limit)),
            ).fetchall()
        items: List[Dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:
                self.delete_pending_delivery(subscriber_id, r["article_url"])
                continue
            if isinstance(payload, dict) and isinstance(payload.get("article"), dict):
                items.append(payload)
            else:
                self.delete_pending_delivery(subscriber_id, r["article_url"])
        return items

    def delete_pending_delivery(self, subscriber_id: int, article_url: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM pending_deliveries WHERE subscriber_id = ? AND article_url = ?",
                (int(subscriber_id), str(article_url)),
            )
            self._conn.commit()

    def count_pending_deliveries(self, subscriber_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM pending_deliveries WHERE subscriber_id = ?",
                (int(subscriber_id),),
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def get_recent_unmarked_deliveries(self, max_age_hours: int = 18, limit: int = 6) -> List[Dict]:
        """
        Fix P1-5 & ChatGPT #3:
        Select ALL unmarked, non-failed deliveries belonging to the top `limit` distinct `article_url`s
        ordered by least-recently-checked (`MIN(last_sold_check_at) ASC, MAX(delivered_at) DESC`).
        Prevents multi-tenant subscriber starvation when >6 subscribers receive the same article.
        """
        cutoff = int(time.time()) - (max_age_hours * 3600)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM deliveries
                WHERE sold_marked = 0
                  AND COALESCE(edit_failed, 0) = 0
                  AND message_id IS NOT NULL
                  AND delivered_at >= ?
                  AND article_url IN (
                      SELECT article_url FROM deliveries
                      WHERE sold_marked = 0
                        AND COALESCE(edit_failed, 0) = 0
                        AND message_id IS NOT NULL
                        AND delivered_at >= ?
                      GROUP BY article_url
                      ORDER BY MIN(last_sold_check_at) ASC, MAX(delivered_at) DESC
                      LIMIT ?
                  )
                ORDER BY last_sold_check_at ASC, delivered_at DESC, id ASC
                """,
                (cutoff, cutoff, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def touch_delivery_sold_check(self, url_or_id: Any) -> None:
        now_ts = int(time.time())
        with self._lock:
            if isinstance(url_or_id, int):
                self._conn.execute(
                    "UPDATE deliveries SET last_sold_check_at = ? WHERE id = ?",
                    (now_ts, url_or_id),
                )
            else:
                self._conn.execute(
                    "UPDATE deliveries SET last_sold_check_at = ? WHERE article_url = ?",
                    (now_ts, str(url_or_id)),
                )
            self._conn.commit()

    def set_delivery_state(self, delivery_id: int, sold_state: str) -> None:
        """F15: record a non-final card state (RESERVED / NO_MAIL / AVAILABLE) without closing follow-up."""
        now_ts = int(time.time())
        with self._lock:
            self._conn.execute(
                "UPDATE deliveries SET sold_state = ?, last_sold_check_at = ? WHERE id = ?",
                (str(sold_state or ""), now_ts, int(delivery_id)),
            )
            self._conn.commit()

    def mark_delivery_sold(self, url_or_id: Any, sold_state: str = "SOLD") -> None:
        now_ts = int(time.time())
        state_val = str(sold_state or "SOLD")
        with self._lock:
            if isinstance(url_or_id, int):
                self._conn.execute(
                    "UPDATE deliveries SET sold_marked = 1, sold_state = ?, last_sold_check_at = ? WHERE id = ?",
                    (state_val, now_ts, url_or_id),
                )
            else:
                self._conn.execute(
                    "UPDATE deliveries SET sold_marked = 1, sold_state = ?, last_sold_check_at = ? WHERE article_url = ?",
                    (state_val, now_ts, str(url_or_id)),
                )
            self._conn.commit()

    def mark_delivery_edit_failed(self, delivery_id: int) -> None:
        now_ts = int(time.time())
        with self._lock:
            self._conn.execute(
                "UPDATE deliveries SET edit_failed = 1, last_sold_check_at = ? WHERE id = ?",
                (now_ts, int(delivery_id)),
            )
            self._conn.commit()

    # =====================================================================
    # Conversation Memory (Fix P2-4: Scoped by user_id:subscriber_id)
    # =====================================================================
    @staticmethod
    def _make_memory_key(user_id: str, subscriber_id: Optional[int] = None) -> str:
        if subscriber_id is not None:
            return f"{user_id}:{subscriber_id}"
        return str(user_id)

    def get_conversation_memory(
        self, user_id: str, subscriber_id: Optional[int] = None
    ) -> List[Dict[str, str]]:
        cutoff = int(time.time()) - MEMORY_TTL_SEC
        mem_key = self._make_memory_key(user_id, subscriber_id)
        with self._lock:
            self._conn.execute(
                "DELETE FROM conversation_memory WHERE created_at < ?", (cutoff,)
            )
            rows = self._conn.execute(
                """
                SELECT role, content FROM conversation_memory
                WHERE user_id = ? AND created_at >= ?
                ORDER BY id DESC LIMIT ?
                """,
                (mem_key, cutoff, MEMORY_MAX_TURNS),
            ).fetchall()
            self._conn.commit()
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def append_conversation_memory(
        self, user_id: str, role: str, content: str, subscriber_id: Optional[int] = None
    ) -> None:
        now_ts = int(time.time())
        mem_key = self._make_memory_key(user_id, subscriber_id)
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversation_memory (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (mem_key, role, content[:1200], now_ts),
            )
            self._conn.commit()

    def clear_conversation_memory(
        self, user_id: str, subscriber_id: Optional[int] = None
    ) -> None:
        with self._lock:
            if subscriber_id is not None:
                mem_key = self._make_memory_key(user_id, subscriber_id)
                self._conn.execute(
                    "DELETE FROM conversation_memory WHERE user_id = ?", (mem_key,)
                )
            else:
                self._conn.execute(
                    "DELETE FROM conversation_memory WHERE user_id = ? OR user_id LIKE ?",
                    (str(user_id), f"{user_id}:%"),
                )
            self._conn.commit()

    # =====================================================================
    # Periodic Cleanup & Daily DB Backup / WAL Checkpoint (P3-9)
    # =====================================================================
    def cleanup_old_articles(self, max_age_days: int = 14) -> int:
        now_ts = int(time.time())
        cutoff_seen = now_ts - (max_age_days * 86400)
        cutoff_cache = now_ts - 7200
        with self._lock:
            cur = self._conn.execute("DELETE FROM seen_articles WHERE seen_at < ?", (cutoff_seen,))
            self._conn.execute("DELETE FROM article_cache WHERE fetched_at < ?", (cutoff_cache,))
            self._conn.execute("DELETE FROM article_ai_cache WHERE created_at < ?", (cutoff_cache,))
            self._conn.execute("DELETE FROM deliveries WHERE delivered_at < ?", (cutoff_seen,))
            # Daily usage counters are only read for the current day; keep 30 days of history
            usage_cutoff = (now_tpe() - timedelta(days=30)).strftime("%Y-%m-%d")
            self._conn.execute("DELETE FROM usage_counters WHERE day < ?", (usage_cutoff,))
            self._conn.execute(
                "DELETE FROM pending_deliveries WHERE created_at < ?",
                (now_ts - PENDING_DELIVERY_MAX_AGE_SEC,),
            )
            self._conn.commit()
            rowcount = cur.rowcount
        self.prune_idle_boards(min_idle_seconds=86400)
        return rowcount

    def backup_database_atomic(self, dest_path: str) -> None:
        """
        V-07: Perform atomic online SQLite WAL backup using sqlite3 backup API.
        Guarantees point-in-time consistency and prevents corrupted backups.
        """
        with self._lock:
            bck_conn = sqlite3.connect(dest_path)
            try:
                self._conn.backup(bck_conn)
            finally:
                bck_conn.close()

    def backup_database(self, max_generations: int = 3) -> str:
        """
        Phase 3.3: Perform atomic online SQLite backup with N-generation rotation
        (.bak.1, .bak.2, .bak.3) using sqlite3.Connection.backup().
        """
        with self._lock:
            for gen in range(max_generations, 1, -1):
                src = f"{self.db_path}.bak.{gen - 1}"
                dst = f"{self.db_path}.bak.{gen}"
                if os.path.exists(src):
                    try:
                        os.replace(src, dst)
                    except Exception:
                        pass
            bak1 = f"{self.db_path}.bak.1"
            self.backup_database_atomic(bak1)
            return bak1

    def run_daily_maintenance(self, backup_dir: Optional[str] = None) -> Optional[str]:
        """
        P3-9 & V-07: Perform WAL checkpoint (TRUNCATE) and create an atomic daily rotating backup in `backups/`
        (retaining the latest 7 daily backups) using conn.backup().
        """
        # N7: day boundary in Asia/Taipei regardless of host TZ
        day_tag = now_tpe().strftime("%Y%m%d")
        last_maint = self.get_setting("last_maintenance_day", "")
        if last_maint == day_tag:
            return None
        # F04: after a failed attempt, retry at most every 30 minutes (this runs every poll cycle)
        if time.time() < getattr(self, "_next_backup_retry_ts", 0.0):
            return None

        if backup_dir is None:
            backup_dir = os.path.join(os.path.dirname(self.db_path), "backups")
        dest_path: Optional[str] = os.path.join(backup_dir, f"bot_data_{day_tag}.db")

        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except Exception as e:
                logger.warning(f"WAL checkpoint 失敗: {e}")
            try:
                os.makedirs(backup_dir, exist_ok=True)
                tmp_path = f"{dest_path}.tmp"
                self.backup_database_atomic(tmp_path)
                # Verify the copy is readable before it replaces anything
                chk = sqlite3.connect(tmp_path)
                try:
                    ok_row = chk.execute("PRAGMA quick_check;").fetchone()
                    chk.execute("SELECT COUNT(*) FROM subscribers").fetchone()
                finally:
                    chk.close()
                if not ok_row or str(ok_row[0]).lower() != "ok":
                    raise RuntimeError(f"備份檔完整性檢查失敗: {ok_row}")
                os.replace(tmp_path, dest_path)
            except Exception as e:
                # F04: do NOT record today's maintenance as done; keep previous backups intact
                logger.error(f"每日資料庫備份失敗，30 分鐘後重試: {e}")
                self._next_backup_retry_ts = time.time() + 1800
                try:
                    if os.path.exists(f"{dest_path}.tmp"):
                        os.remove(f"{dest_path}.tmp")
                except Exception:
                    pass
                return None
            self.set_setting("last_maintenance_day", day_tag)
            self._next_backup_retry_ts = 0.0

        # Retain latest 7 backups
        try:
            backups = sorted(glob.glob(os.path.join(backup_dir, "bot_data_*.db")))
            for old_f in backups[:-7]:
                try:
                    os.remove(old_f)
                except Exception:
                    pass
        except Exception:
            pass

        return dest_path
