import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import random
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from dotenv import load_dotenv

from config import (
    DB_PATH,
    DEFAULT_BOARD,
    DEFAULT_CHANNEL_ID,
    DEFAULT_GUILD_ID,
    DISCORD_TOKEN,
    GROQ_API_KEY,
    GROQ_FAST_MODEL,
    GROQ_MODEL,
    LOG_PATH,
    MAX_GLOBAL_ACTIVE_BOARDS,
    OWNER_USER_ID,
    POLL_MAX,
    POLL_MIN,
)
from delivery import DeliveryManager
from embeds import (
    ArticleLinkView,
    PaginatedEmbedView,
    build_article_embed,
    enforce_embed_limits,
)
from groq_agent import GroqAgent
from groq_scheduler import GroqScheduler, P1_REALTIME
from ptt_crawler import COMMON_BOARDS_MAP, PTTCrawler
from storage import BotStorage, extract_first_price_int

# Enforce secure umask for all created database and log files (V-02 / CWE-732 mitigation)
try:
    os.umask(0o077)
except Exception:
    pass

# Setup Rotating File Logging (C1 & P1-6: max 5MB x 3 backups; only attach StreamHandler on interactive TTY or Docker)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_log_dir = os.path.dirname(os.path.abspath(LOG_PATH))
if _log_dir:
    os.makedirs(_log_dir, exist_ok=True)

_log_handlers: List[logging.Handler] = [
    RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
]
if sys.stdout.isatty() or os.getenv("LOG_TO_STDOUT", "").strip() == "1":
    _log_handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("ptt_dc_bot")

load_dotenv(os.path.join(BASE_DIR, ".env"))


SCOPE_CHOICES = [
    app_commands.Choice(name="🔄 自動判斷 (私訊=個人 / 頻道=伺服器)", value="auto"),
    app_commands.Choice(name="👤 個人專屬追蹤 (推播至我的 Discord 私訊)", value="user"),
    app_commands.Choice(name="🏠 伺服器共用追蹤 (推播至伺服器通知頻道)", value="guild"),
]


def is_bot_owner(user_id: Any) -> bool:
    uid_str = str(user_id)
    if not uid_str:
        return False
    if OWNER_USER_ID and uid_str == str(OWNER_USER_ID):
        return True
    detected = getattr(globals().get("bot"), "detected_owner_id", "")
    return bool(detected and uid_str == str(detected))


def has_guild_write_permission(
    user: Any, guild: Optional[discord.Guild]
) -> bool:
    """
    Fix P0-2 & P0-3:
    Strict permission check for modifying guild-scoped ('guild') shared rules.
    Never grants write access to all members of DEFAULT_GUILD_ID indiscriminately.
    Allows:
      1. Configured OWNER_USER_ID
      2. Guild owner (`guild.owner_id == user.id`)
      3. Members with `administrator`, `manage_guild`, or `manage_channels` permission
    """
    if is_bot_owner(getattr(user, "id", "")):
        return True
    if guild is None:
        return False
    if getattr(guild, "owner_id", None) and getattr(user, "id", None) == guild.owner_id:
        return True
    perms = getattr(user, "guild_permissions", None)
    if perms and (
        perms.administrator or perms.manage_guild or perms.manage_channels
    ):
        return True
    return False


def build_welcome_dm_embed(display_name: str) -> discord.Embed:
    """
    Fix P3-3: Static zero-token welcome card sent the first time a friend DMs the bot.
    """
    embed = discord.Embed(
        title=f"👋 歡迎使用 PTT 智慧追蹤機器人，{display_name}！",
        description=(
            "這裡是您的 **👤 個人專屬追蹤空間**。您在私訊中設定的所有關鍵字與 AI 模糊規則皆完全獨立，"
            "一旦 PTT 有符合條件且**尚未售出**的新文章上架，機器人會直接傳送私訊通知您！"
        ),
        color=discord.Color.from_rgb(46, 204, 113),
        timestamp=datetime.now(),
    )
    embed.add_field(
        name="💬 您可以直接用自然語言對我說：",
        value=(
            "• `幫我追蹤 HardwareSale 板販售的 3060TI 跟 6700XT`\n"
            "• `幫我設定模糊追蹤：預算 6500 元以下、有保固的 RTX 3060Ti`\n"
            "• `幫我掃描目前 HardwareSale 板還沒賣掉的 3060TI`\n"
            "• `幫我找買賣二手筆電或 MacBook 的看板並加入追蹤`"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛠️ 常用快捷指令",
        value=(
            "• `/me`：查看個人規則清單与今日剩餘配額\n"
            "• `/keyword_add` / `/fuzzy_add`：不消耗 AI 對話額度快速新增規則\n"
            "• `/pause` / `/resume`：暫停或恢復私訊推播通知\n"
            "• `/forget_me`：一鍵清除您的所有個人資料與記憶"
        ),
        inline=False,
    )
    embed.set_footer(text="👤 個人專屬追蹤 | 雙層規則引擎 + Groq 多模型語意過濾")
    return embed


class PTTDiscordBot(discord.Client):
    def __init__(
        self,
        storage: BotStorage,
        crawler: PTTCrawler,
        scheduler: GroqScheduler,
        agent: GroqAgent,
    ):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.storage = storage
        self.crawler = crawler
        self.scheduler = scheduler
        self.agent = agent
        self.delivery = DeliveryManager(bot=self, storage=storage, crawler=crawler)
        self._poll_task: Optional[asyncio.Task] = None
        self._poll_lock = asyncio.Lock()
        self.next_poll_time: Optional[datetime] = None
        self._synced_guilds = set()
        self._global_synced = False
        self._last_manual_check_ts: float = 0.0
        self._last_board_alert_ts: Dict[str, float] = {}
        self.detected_owner_id: str = str(OWNER_USER_ID or "")

    async def setup_hook(self) -> None:
        self._poll_task = asyncio.create_task(self._polling_loop())

    async def close(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        await self.agent.close()
        await self.crawler.close()
        self.storage.close()
        await super().close()

    async def on_ready(self) -> None:
        logger.info(f"機器人已登入 Discord: {self.user} (ID: {self.user.id})")
        logger.info(f"目前所在伺服器數量: {len(self.guilds)}")

        # Sync global commands once so DM / User-install / Guild slash commands work everywhere
        if not self._global_synced:
            try:
                g_synced = await self.tree.sync()
                self._global_synced = True
                logger.info(
                    f"已全域同步 {len(g_synced)} 個 Slash 指令 (支援私訊 DM 與跨伺服器使用)"
                )
            except Exception as e:
                logger.warning(f"全域同步 Slash 指令失敗: {e}")

        for guild in self.guilds:
            # Auto-detect owner ID from the primary home guild if OWNER_USER_ID is not set in .env
            if str(guild.id) == str(DEFAULT_GUILD_ID) and getattr(guild, "owner_id", None):
                if not self.detected_owner_id:
                    self.detected_owner_id = str(guild.owner_id)
                    logger.info(f"已從主伺服器自動識別擁有者 User ID: {self.detected_owner_id}")
                owner_sub = self.storage.get_subscriber("user", str(guild.owner_id))
                if owner_sub and owner_sub.get("tier") != "owner":
                    self.storage.set_subscriber_tier("user", str(guild.owner_id), "owner")

            # Ensure a guild subscriber row exists for joined guilds
            self.storage.get_or_create_subscriber(
                "guild",
                str(guild.id),
                display_name=guild.name,
                default_push_channel_id=DEFAULT_CHANNEL_ID
                if str(guild.id) == DEFAULT_GUILD_ID
                else None,
            )
            if guild.id not in self._synced_guilds:
                try:
                    # Clear guild-copied duplicates so global commands don't appear twice in the server
                    self.tree.clear_commands(guild=guild)
                    await self.tree.sync(guild=guild)
                    self._synced_guilds.add(guild.id)
                    logger.info(
                        f"已確認伺服器指令同步完成 (清理重複指令並使用全域指令): {guild.name} ({guild.id})"
                    )
                except Exception as e:
                    logger.warning(f"同步指令至伺服器 {guild.name} 失敗: {e}")

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="PTT 看板 | 私訊或 /ai 開始追蹤",
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        try:
            default_ch_id = None
            if (
                guild.system_channel
                and guild.system_channel.permissions_for(guild.me).send_messages
            ):
                default_ch_id = str(guild.system_channel.id)
            else:
                for ch in guild.text_channels:
                    if ch.permissions_for(guild.me).send_messages:
                        default_ch_id = str(ch.id)
                        break

            self.storage.get_or_create_subscriber(
                "guild",
                str(guild.id),
                display_name=guild.name,
                default_push_channel_id=default_ch_id,
            )
            self._synced_guilds.add(guild.id)
            logger.info(f"加入新伺服器並初始化訂閱設定: {guild.name} ({guild.id})")
        except Exception as e:
            logger.warning(f"新伺服器初始化失敗: {e}")

    async def _send_board_failure_alert(
        self, board_name: str, failures: int, status_msg: str
    ) -> None:
        """
        Fix P3-2: Alert owner or default channel when a board fails >= 5 consecutive times (throttled to 1h).
        """
        now = time.time()
        if now - self._last_board_alert_ts.get(board_name, 0.0) < 3600:
            return
        self._last_board_alert_ts[board_name] = now

        alert_text = (
            f"⚠️ **PTT 看板巡檢異常警報**\n"
            f"看板 `{board_name}` 已連續 `{failures}` 次抓取失敗（最新狀態：`{status_msg}`）。\n"
            f"系統已自動保留 `seen_articles` 基準（不會重置或吞沒文章），將於下一輪繼續重試。"
        )
        try:
            owner_uid = OWNER_USER_ID or self.detected_owner_id
            if owner_uid and str(owner_uid).isdigit():
                owner_user = self.get_user(int(owner_uid)) or await self.fetch_user(
                    int(owner_uid)
                )
                if owner_user:
                    dm = owner_user.dm_channel or await owner_user.create_dm()
                    await dm.send(alert_text)
                    return
            target = await self.resolve_push_channel(subscriber_id=1)
            if target:
                await target.send(alert_text)
        except Exception as e:
            logger.debug(f"發送看板異常警報略過: {e}")

    async def on_message(self, message: discord.Message) -> None:
        """
        Supports natural language chat with the Groq AI Assistant in two ways:
        1. Direct Message (DM) to the bot (`message.guild is None`) — Friends can DM the bot directly to start using their personal tracker!
        2. @Mentioning the bot inside any server channel (`self.user in message.mentions`).
        """
        if message.author.bot or not self.user:
            return

        is_dm = message.guild is None
        is_mentioned = self.user in message.mentions
        if not is_dm and not is_mentioned:
            return

        user_id_str = str(message.author.id)
        if self.storage.is_blacklisted(user_id_str):
            return

        cleaned = re.sub(rf"<@!?{self.user.id}>", "", message.content).strip()
        if is_dm:
            sub, is_new_sub = self.storage.get_or_create_subscriber_with_flag(
                "user", user_id_str, display_name=message.author.display_name
            )
            if is_bot_owner(user_id_str) and sub.get("tier") != "owner":
                self.storage.set_subscriber_tier("user", user_id_str, "owner")
                sub["tier"] = "owner"
            if sub.get("dm_failed"):
                self.storage.mark_subscriber_dm_failed(sub["id"], False)
            scope_label = f"👤 個人專屬追蹤 ({message.author.display_name})"
            can_write = True

            # Fix P3-3: Send static welcome guide on first DM
            if is_new_sub:
                try:
                    await message.channel.send(
                        embed=build_welcome_dm_embed(message.author.display_name)
                    )
                    if not cleaned or cleaned in ("你好", "嗨", "hi", "hello", "測試"):
                        return
                except Exception:
                    pass
        else:
            # In a guild channel: if user asks for personal/DM tracking, use personal scope; otherwise server scope
            wants_personal = bool(
                re.search(r"(私訊我|個人追蹤|私訊通知|傳私訊)", cleaned)
            )
            if wants_personal:
                sub = self.storage.get_or_create_subscriber(
                    "user", user_id_str, display_name=message.author.display_name
                )
                scope_label = f"👤 個人專屬追蹤 ({message.author.display_name})"
                can_write = True
            else:
                sub = self.storage.get_or_create_subscriber(
                    "guild",
                    str(message.guild.id),
                    display_name=message.guild.name,
                )
                scope_label = f"🏠 伺服器追蹤 ({message.guild.name})"
                # Fix P0-2: Enforce guild write permission for @mention in guild channels!
                can_write = has_guild_write_permission(message.author, message.guild)

        if not cleaned:
            cleaned = (
                "你好！請用簡短條列方式介紹你能幫我做什麼（包含如何直接用自然語言設定關鍵字、AI模糊預算追蹤、掃描過去還沒賣掉的商品），"
                "並列出我目前的追蹤設定。"
            )

        if not self.scheduler.acquire_user_slot(user_id_str, "ai"):
            await message.reply(
                "⏳ 您目前已有一個 AI 任務正在處理中，請稍候幾秒待上一則回覆完成！",
                mention_author=False,
            )
            return

        try:
            allowed, used_cnt, limit_cnt = self.storage.check_and_increment_usage(
                sub["id"], "ai_chat"
            )
            if not allowed:
                await message.reply(
                    f"⚠️ 已達今日 AI 對話次數上限（`{used_cnt}/{limit_cnt}` 次）。\n"
                    f"💡 您仍可隨時使用不消耗 AI 對話額度的 Slash 指令（如 `/keyword_add`、`/keyword_list`、`/search`）！",
                    mention_author=False,
                )
                return

            q_status = self.scheduler.get_queue_status()
            wait_msg: Optional[discord.Message] = None
            if q_status["queue_size"] > 0:
                wait_msg = await message.reply(
                    f"⏳ AI 智慧助手排隊處理中（目前佇列 `{q_status['queue_size']}` 筆，預計等待約 `{q_status['estimated_wait_sec']}s`）...",
                    mention_author=False,
                )

            async with message.channel.typing():
                answer, steps = await self.agent.run_agent(
                    cleaned,
                    subscriber_id=sub["id"],
                    user_id=user_id_str,
                    scope_label=scope_label,
                    can_write=can_write,
                )
                embed = discord.Embed(
                    title="🤖 PTT AI 智慧助手回覆",
                    description=answer[:3900],
                    color=discord.Color.from_rgb(155, 89, 182),
                    timestamp=datetime.now(),
                )
                if steps:
                    embed.add_field(
                        name=f"🛠️ AI 自動執行的工具步驟 ({len(steps)})",
                        value="\n".join(steps)[:1000],
                        inline=False,
                    )
                embed.set_footer(
                    text=f"{scope_label} | 今日 AI 額度: {used_cnt}/{limit_cnt} | Groq 多模型排程"
                )
                safe_embed = enforce_embed_limits(embed)
                if wait_msg:
                    await wait_msg.edit(content=None, embed=safe_embed)
                else:
                    await message.reply(
                        embed=safe_embed,
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
        except Exception as e:
            logger.error(f"處理 AI 對話失敗: {e}", exc_info=True)
            await message.reply(
                "❌ 處理時發生系統錯誤，已記錄於內部日誌。若持續發生請洽管理員。",
                mention_author=False,
            )
        finally:
            self.scheduler.release_user_slot(user_id_str, "ai")

    async def resolve_push_channel(
        self, subscriber_id: int = 1
    ) -> Optional[discord.abc.Messageable]:
        sub = self.storage.get_subscriber_by_id(subscriber_id)
        if not sub:
            return None
        return await self.delivery.resolve_subscriber_target(sub)

    async def _polling_loop(self) -> None:
        await self.wait_until_ready()
        logger.info("背景 PTT 多租戶精準盯梢巡檢迴圈已啟動 (Crawl-Once Fan-Out)")
        await asyncio.sleep(3)

        while not self.is_closed():
            try:
                await self.run_single_poll_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"巡檢迴圈發生未預期錯誤: {e}", exc_info=True)

            try:
                self.storage.cleanup_old_articles(max_age_days=14)
                self.storage.run_daily_maintenance()
            except Exception:
                pass

            sleep_sec = random.uniform(POLL_MIN, POLL_MAX)
            self.next_poll_time = datetime.fromtimestamp(
                datetime.now().timestamp() + sleep_sec
            )
            logger.info(f"本輪巡檢結束，下次檢查將於 {int(sleep_sec)} 秒後進行")
            await asyncio.sleep(sleep_sec)

    async def run_single_poll_cycle(
        self, target_board: Optional[str] = None
    ) -> Dict[str, int]:
        """
        Crawl-Once, Fan-Out Poll Cycle (Upgraded for v2 Review):
        1. Scans each active board once; never resets `initialized` on transient errors (P0-1).
        2. Evaluates unseen articles against all enabled subscribers' exact keywords & fuzzy rules.
        3. Fetches keyword-agnostic article base detail once, then applies per-token view and AI verification (P1-1, P1-2).
        4. Dispatches matched notifications per subscriber (Guild channel or User DM) via DeliveryManager.
        5. Periodically checks recently delivered articles in round-robin order and updates their embed if sold out (P1-5).
        """
        summary = {"checked_boards": 0, "new_articles": 0, "pushed": 0}
        async with self._poll_lock:
            boards = self.storage.get_boards()
            if target_board:
                boards = [
                    b for b in boards if b["name"].lower() == target_board.lower()
                ]

            if not boards:
                return summary

            enabled_subs = self.storage.get_all_enabled_subscribers()
            sub_map: Dict[int, Dict[str, Any]] = {s["id"]: s for s in enabled_subs}
            all_mode_sub_ids = [
                s["id"] for s in enabled_subs if s.get("push_mode") == "all"
            ]

            for idx, board_info in enumerate(boards):
                board_name = board_info["name"]
                needs_reinit = self.storage.should_reinit_board(board_info)

                # Only scan boards that have active rules across any enabled subscriber
                if not self.storage.has_active_rules_for_board(board_name):
                    self.storage.update_board_status(
                        board_name,
                        False,
                        "待機中 (目前無訂閱者設定此看板規則，已跳過掃描)",
                        scanned_now=False,
                        reset_baseline=True,
                    )
                    continue

                if idx > 0:
                    await asyncio.sleep(random.uniform(3.2, 6.5))

                in_cd, remain = self.crawler.is_in_cooldown()
                if in_cd:
                    self.storage.update_board_status(
                        board_name,
                        not needs_reinit,
                        f"防封鎖冷卻中 (剩餘 {remain}s)",
                        scanned_now=False,
                        reset_baseline=False,
                        is_error=False,
                    )
                    continue

                ok, articles, status_msg = await self.crawler.fetch_board_articles(
                    board_name,
                    fetch_prev_if_few=(not needs_reinit),
                    max_pages=1,
                )
                summary["checked_boards"] += 1

                if not ok:
                    failures = self.storage.update_board_status(
                        board_name,
                        not needs_reinit,
                        f"異常: {status_msg}",
                        scanned_now=False,
                        reset_baseline=False,
                        is_error=True,
                    )
                    logger.warning(
                        f"看板 {board_name} 抓取失敗 (連續 {failures} 次): {status_msg}"
                    )
                    if failures >= 5:
                        await self._send_board_failure_alert(
                            board_name, failures, status_msg
                        )
                    continue

                # Baseline silently when a board is first initialized or resumed from 0-subscriber idle
                if needs_reinit:
                    added = self.storage.mark_articles_seen(articles)
                    self.storage.update_board_status(
                        board_name,
                        True,
                        f"已完成基準同步 ({len(articles)} 篇)",
                        scanned_now=True,
                    )
                    logger.info(
                        f"看板 【{board_name}】 完成基準同步，已記錄 {added} 篇現有文章"
                    )
                    continue

                new_articles = self.storage.filter_unseen_articles(articles)
                if not new_articles:
                    self.storage.update_board_status(
                        board_name, True, "正常盯梢中 (無新文章)", scanned_now=True
                    )
                    continue

                self.storage.mark_articles_seen(new_articles)
                if len(new_articles) > 20:
                    logger.info(
                        f"看板 {board_name} 單輪新文章達 {len(new_articles)} 篇，僅處理最新 20 篇以防洪水推播"
                    )
                    new_articles = new_articles[-20:]
                summary["new_articles"] += len(new_articles)

                board_fuzzy_rules = self.storage.get_fuzzy_rules(board=board_name)
                per_sub_queue: Dict[int, List[Dict[str, Any]]] = {}
                matched_articles_count = 0

                for art in new_articles:
                    kw_hits = self.storage.match_article_keywords_detailed(
                        board_name, art["title"]
                    )
                    # Determine if any fuzzy rule applies to this article's category
                    applicable_fuzzy = [
                        r
                        for r in board_fuzzy_rules
                        if r["category"].upper() == "ALL"
                        or r["category"].lower() in art.get("category", "").lower()
                    ]

                    needs_detail = (
                        bool(kw_hits)
                        or bool(applicable_fuzzy)
                        or bool(all_mode_sub_ids)
                    )
                    if not needs_detail:
                        continue

                    # Fix P1-1: Fetch keyword-agnostic base detail first
                    base_detail = await self.crawler.fetch_article_full_detail(
                        art["url"],
                        article_title=art["title"],
                        article_author=art.get("author", ""),
                        target_keyword="",
                    )
                    is_selling_post = "賣" in art.get("category", "") or "售" in art.get(
                        "category", ""
                    )

                    # Fix P1-2: Evaluate each unique primary_token independently
                    token_eval: Dict[str, Tuple[bool, Dict[str, Any]]] = {}
                    for kh in kw_hits:
                        tok = kh["primary_token"]
                        if tok in token_eval:
                            continue
                        tok_detail = PTTCrawler.apply_keyword_view(base_detail, tok)
                        if tok_detail.get("is_sold") and is_selling_post:
                            logger.info(
                                f"略過已售出/洽中文章 [{tok}]: [{board_name}] {art['title']} ({tok_detail.get('sold_reason')})"
                            )
                            token_eval[tok] = (False, tok_detail)
                            continue

                        if is_selling_post:
                            ai_check = await self.agent.verify_unsold_semantic(
                                art, tok_detail, tok, priority=P1_REALTIME
                            )
                            if not ai_check.get("is_available", True):
                                logger.info(
                                    f"略過 AI 語意判定不可買文章 [{tok}] ({ai_check.get('model')}): [{board_name}] {art['title']} -> {ai_check.get('reason')}"
                                )
                                token_eval[tok] = (False, tok_detail)
                                continue
                            if ai_check.get("target_price"):
                                tok_detail["extracted_price"] = ai_check["target_price"]
                            if ai_check.get("model"):
                                tok_detail["ai_verified_by"] = ai_check["model"]
                            note = ai_check.get("summary_note") or ai_check.get("reason")
                            if note:
                                tok_detail["ai_summary_note"] = note

                        token_eval[tok] = (True, tok_detail)

                    sub_kw_labels: Dict[int, List[str]] = {}
                    sub_best_detail: Dict[int, Dict[str, Any]] = {}
                    for kh in kw_hits:
                        tok = kh["primary_token"]
                        avail, tok_detail = token_eval.get(tok, (False, base_detail))
                        if not avail:
                            continue
                        sid = kh["subscriber_id"]
                        max_p = kh.get("max_price")
                        if max_p is not None:
                            extracted_price_int = extract_first_price_int(
                                tok_detail.get("extracted_price")
                            )
                            if (
                                extracted_price_int is not None
                                and extracted_price_int > int(max_p)
                            ):
                                continue
                        sub_kw_labels.setdefault(sid, []).append(kh["label"])
                        sub_best_detail.setdefault(sid, tok_detail)

                    # Evaluate all subscribers' fuzzy rules if the whole post isn't already marked sold
                    fuzzy_hits_all: List[Dict[str, Any]] = []
                    whole_post_sold = bool(base_detail.get("is_sold") and is_selling_post)
                    if applicable_fuzzy and not whole_post_sold:
                        fuzzy_hits_all = await self.agent.evaluate_article_fuzzy_rules(
                            art, base_detail, applicable_fuzzy
                        )

                    sub_fuzzy_map: Dict[int, List[Dict[str, Any]]] = {}
                    for fh in fuzzy_hits_all:
                        sid = fh.get("subscriber_id", 1)
                        sub_fuzzy_map.setdefault(sid, []).append(fh)
                        sub_best_detail.setdefault(sid, base_detail)

                    active_all_mode_sids = (
                        set(all_mode_sub_ids) if not whole_post_sold else set()
                    )
                    for sid in active_all_mode_sids:
                        sub_best_detail.setdefault(sid, base_detail)

                    target_sids = (
                        set(sub_kw_labels.keys())
                        | set(sub_fuzzy_map.keys())
                        | active_all_mode_sids
                    )
                    if target_sids:
                        matched_articles_count += 1
                        for sid in target_sids:
                            if sid not in sub_map:
                                continue
                            per_sub_queue.setdefault(sid, []).append(
                                {
                                    "article": art,
                                    "matched_keywords": sub_kw_labels.get(sid, []),
                                    "detail": sub_best_detail.get(sid, base_detail),
                                    "fuzzy_matches": sub_fuzzy_map.get(sid, []),
                                }
                            )

                self.storage.update_board_status(
                    board_name,
                    True,
                    f"正常盯梢中 (新文 {len(new_articles)} 篇 / 命中 {matched_articles_count} 篇)",
                    scanned_now=True,
                )

                # Deliver to each matched subscriber
                for sid, items in per_sub_queue.items():
                    sub = sub_map.get(sid)
                    if not sub:
                        continue
                    pushed_cnt = await self.delivery.deliver_subscriber_matches(
                        sub, items
                    )
                    summary["pushed"] += pushed_cnt

            # D1 & P1-5: Periodically check up to 2 recent delivered articles in round-robin order
            try:
                await self.delivery.check_sold_deliveries_once(max_checks=2)
            except Exception as e:
                logger.debug(f"已售出狀態追蹤檢查略過: {e}")

        return summary


storage = BotStorage(
    db_path=DB_PATH,
    default_channel_id=DEFAULT_CHANNEL_ID,
    default_board=DEFAULT_BOARD,
    default_guild_id=DEFAULT_GUILD_ID,
)
crawler = PTTCrawler(min_request_delay=2.0, max_request_delay=4.2, storage=storage)
scheduler = GroqScheduler(api_key=GROQ_API_KEY)
agent = GroqAgent(
    api_key=GROQ_API_KEY,
    storage=storage,
    crawler=crawler,
    primary_model=GROQ_MODEL,
    fast_model=GROQ_FAST_MODEL,
    scheduler=scheduler,
)
bot = PTTDiscordBot(
    storage=storage, crawler=crawler, scheduler=scheduler, agent=agent
)


# =====================================================================
# Global CommandTree Error Handler (C4) & Scope / Permission Helper (A2)
# =====================================================================
@bot.tree.error
async def on_tree_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    logger.error(f"Slash 指令執行錯誤 ({interaction.command}): {error}", exc_info=True)
    msg = "❌ 指令執行時發生系統錯誤，已記錄於內部日誌。若持續發生請洽管理員。"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


def resolve_interaction_subscriber(
    interaction: discord.Interaction,
    scope_choice: str = "auto",
    require_write_perm: bool = False,
) -> Tuple[Optional[Dict[str, Any]], str, Optional[str]]:
    """
    Resolve which subscriber ('user' vs 'guild') an interaction operates on.
    Returns (subscriber_dict, scope_label, error_message_if_denied).
    """
    user = interaction.user
    uid_str = str(user.id)

    if bot.storage.is_blacklisted(uid_str):
        return None, "", "🚫 您的帳號目前已被限制使用本機器人。"

    is_dm = interaction.guild is None
    if is_dm or scope_choice == "user":
        sub = bot.storage.get_or_create_subscriber(
            "user", uid_str, display_name=user.display_name
        )
        if is_bot_owner(uid_str) and sub.get("tier") != "owner":
            bot.storage.set_subscriber_tier("user", uid_str, "owner")
            sub["tier"] = "owner"
        if sub.get("dm_failed"):
            bot.storage.mark_subscriber_dm_failed(sub["id"], False)
        return sub, f"👤 個人專屬追蹤 ({user.display_name})", None

    # Otherwise scope is 'guild'
    guild = interaction.guild
    gid_str = str(guild.id)

    if require_write_perm:
        if not has_guild_write_permission(user, guild):
            return (
                None,
                "",
                "🔒 修改「伺服器共用追蹤」需要伺服器的 `管理伺服器 (Manage Server)` 或 `管理頻道 (Manage Channels)` 權限！\n"
                "💡 **想要建立您個人的專屬通知嗎？** 請在指令的 `scope` 選擇 **「👤 個人專屬追蹤」**，或直接**私訊 (DM)** 本機器人即可免費使用！",
            )

    default_ch = str(DEFAULT_CHANNEL_ID) if gid_str == str(DEFAULT_GUILD_ID) else None
    if not default_ch and interaction.channel_id:
        default_ch = str(interaction.channel_id)

    sub = bot.storage.get_or_create_subscriber(
        "guild",
        gid_str,
        display_name=guild.name,
        default_push_channel_id=default_ch,
    )
    return sub, f"🏠 伺服器共用追蹤 ({guild.name})", None


# =====================================================================
# Autocomplete Helpers (Tenant-Scoped & Read-Only! Fix P2-3)
# =====================================================================
async def board_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    boards = bot.storage.get_boards()
    choices = []
    for b in boards:
        if current.lower() in b["name"].lower():
            choices.append(app_commands.Choice(name=b["name"], value=b["name"]))
    return choices[:25]


async def keyword_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    uid_str = str(interaction.user.id)
    user_sub = bot.storage.get_subscriber("user", uid_str)
    keywords = (
        list(bot.storage.get_keywords(subscriber_id=user_sub["id"]))
        if user_sub
        else []
    )
    user_sub_id = user_sub["id"] if user_sub else -1

    if interaction.guild:
        guild_sub = bot.storage.get_subscriber("guild", str(interaction.guild.id))
        if guild_sub:
            for gk in bot.storage.get_keywords(subscriber_id=guild_sub["id"]):
                keywords.append(gk)

    choices = []
    seen_ids = set()
    for kw in keywords:
        if kw["id"] in seen_ids:
            continue
        seen_ids.add(kw["id"])
        tag = "👤個人" if kw["subscriber_id"] == user_sub_id else "🏠伺服器"
        display = f"#{kw['id']} [{tag}] {kw['keyword']} ({kw['board']}/{kw['category']})"
        if current.lower() in display.lower():
            choices.append(app_commands.Choice(name=display[:100], value=str(kw["id"])))
    return choices[:25]


async def fuzzy_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    uid_str = str(interaction.user.id)
    user_sub = bot.storage.get_subscriber("user", uid_str)
    rules = (
        list(bot.storage.get_fuzzy_rules(subscriber_id=user_sub["id"]))
        if user_sub
        else []
    )
    user_sub_id = user_sub["id"] if user_sub else -1

    if interaction.guild:
        guild_sub = bot.storage.get_subscriber("guild", str(interaction.guild.id))
        if guild_sub:
            for gr in bot.storage.get_fuzzy_rules(subscriber_id=guild_sub["id"]):
                rules.append(gr)

    choices = []
    seen_ids = set()
    for r in rules:
        if r["id"] in seen_ids:
            continue
        seen_ids.add(r["id"])
        tag = "👤個人" if r["subscriber_id"] == user_sub_id else "🏠伺服器"
        display = f"#{r['id']} [{tag}] {r['description'][:55]} ({r['board']})"
        if current.lower() in display.lower():
            choices.append(app_commands.Choice(name=display[:100], value=str(r["id"])))
    return choices[:25]


# =====================================================================
# Slash Command 1: AI Natural Language Assistant (/ai)
# =====================================================================
@bot.tree.command(
    name="ai",
    description="🧠 用自然語言請 AI 助手幫你尋找看板、設定模糊/精確追蹤、或掃描版上未售出的商品！",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    prompt="用自然語言輸入需求（例如：幫我掃描還沒賣掉的 3060TI，或幫我追蹤 6500 元以下的 3060TI）",
    scope="選擇要操作「個人專屬追蹤(私訊)」或「伺服器共用追蹤」",
)
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_ai(
    interaction: discord.Interaction,
    prompt: str,
    scope: str = "auto",
):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=False
    )
    if err or not sub:
        await interaction.response.send_message(
            err or "❌ 無法識別訂閱身分", ephemeral=True
        )
        return

    can_write = (
        True
        if sub.get("scope") == "user"
        else has_guild_write_permission(interaction.user, interaction.guild)
    )

    uid_str = str(interaction.user.id)
    if not bot.scheduler.acquire_user_slot(uid_str, "ai"):
        await interaction.response.send_message(
            "⏳ 您目前已有一個 AI 任務正在執行中，請稍候幾秒鐘再試！", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=False)
    try:
        allowed, used_cnt, limit_cnt = bot.storage.check_and_increment_usage(
            sub["id"], "ai_chat"
        )
        if not allowed:
            await interaction.followup.send(
                f"⚠️ 已達今日 AI 智慧助手額度上限（`{used_cnt}/{limit_cnt}` 次）。您仍可使用 `/keyword_add`、`/search` 等指令！"
            )
            return

        answer, steps = await bot.agent.run_agent(
            prompt,
            subscriber_id=sub["id"],
            user_id=uid_str,
            scope_label=scope_label,
            can_write=can_write,
        )
        embed = discord.Embed(
            title="🧠 PTT AI 智慧助手",
            description=answer[:3800],
            color=discord.Color.from_rgb(155, 89, 182),
            timestamp=datetime.now(),
        )
        embed.add_field(name="💬 您的指令", value=f"`{prompt[:300]}`", inline=False)
        if steps:
            embed.add_field(
                name=f"🔄 AI 遞迴執行的工具步驟 ({len(steps)})",
                value="\n".join(steps)[:1000],
                inline=False,
            )
        embed.set_footer(
            text=f"{scope_label} | 今日 AI 額度: {used_cnt}/{limit_cnt} | Groq 多模型排程"
        )
        await interaction.followup.send(embed=enforce_embed_limits(embed))
    except Exception as e:
        logger.error(f"/ai 指令執行異常: {e}", exc_info=True)
        await interaction.followup.send("❌ 處理時發生系統錯誤，已記錄於內部日誌。若持續發生請洽管理員。")
    finally:
        bot.scheduler.release_user_slot(uid_str, "ai")


# =====================================================================
# Slash Command 2: Scan Past Unsold Articles (/scan_unsold)
# =====================================================================
@bot.tree.command(
    name="scan_unsold",
    description="🕵️ 掃描並篩選過去符合條件且『尚未標示售出（還在賣）』的商品！",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    keyword="要掃描的關鍵字（若不填，預設自動掃描您目前設定的所有追蹤關鍵字！）",
    board="要掃描的 PTT 看板（預設 HardwareSale）",
    max_per_keyword="每個關鍵字最多回傳幾篇確定可買的近期文章（1~8，預設 5）",
    push_to_channel="是否同時將找到的未售出商品卡片推播到通知頻道/私訊（預設 False）",
    scope="若未填 keyword，要讀取哪裡的關鍵字清單（預設自動判斷）",
)
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_scan_unsold(
    interaction: discord.Interaction,
    keyword: Optional[str] = None,
    board: str = "HardwareSale",
    max_per_keyword: app_commands.Range[int, 1, 8] = 5,
    push_to_channel: bool = False,
    scope: str = "auto",
):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=False
    )
    if err or not sub:
        await interaction.response.send_message(
            err or "❌ 無法識別訂閱身分", ephemeral=True
        )
        return

    uid_str = str(interaction.user.id)
    if not bot.scheduler.acquire_user_slot(uid_str, "scan"):
        await interaction.response.send_message(
            "⏳ 您目前已有一個掃描任務正在進行中，請稍候完成再試！", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=False)
    try:
        if board.lower() in COMMON_BOARDS_MAP:
            board = COMMON_BOARDS_MAP[board.lower()]

        if keyword and keyword.strip():
            target_keywords = [
                k.strip() for k in re.split(r"[,，]+", keyword) if k.strip()
            ]
        else:
            saved_kws = bot.storage.get_keywords(board=board, subscriber_id=sub["id"])
            target_keywords = [k["keyword"] for k in saved_kws]

        if not target_keywords:
            await interaction.followup.send(
                f"⚠️ 在 **{scope_label}** 下尚未設定任何 `{board}` 關鍵字，請在指令中填入 `keyword` 參數，或先透過 `/keyword_add` 新增！"
            )
            return

        allowed, used_cnt, limit_cnt = bot.storage.check_and_increment_usage(
            sub["id"], "scan"
        )
        if not allowed:
            await interaction.followup.send(
                f"⚠️ 已達今日深度掃描次數上限（`{used_cnt}/{limit_cnt}` 次）。"
            )
            return

        unsold_items = await bot.crawler.scan_unsold_items(
            board=board,
            keywords=target_keywords,
            max_per_keyword=max_per_keyword,
            category_filter="賣",
            ai_semantic_verifier=bot.agent.verify_unsold_semantic,
        )

        # Build paginated embeds if many items found (up to 6 items per page)
        pages: List[discord.Embed] = []
        chunk_size = 6
        item_chunks = (
            [
                unsold_items[i : i + chunk_size]
                for i in range(0, len(unsold_items), chunk_size)
            ]
            if unsold_items
            else [[]]
        )

        for page_idx, chunk in enumerate(item_chunks, start=1):
            embed = discord.Embed(
                title=f"🕵️ PTT 【{board}】過去未售出商品掃描結果",
                description=(
                    f"🎯 **掃描目標關鍵字**：{'、'.join(f'`{k}`' for k in target_keywords)}\n"
                    f"🔍 **雙重過濾機制**：21天內文章 + 規則引擎 + **多模型交替 AI 語意審核 (`gpt-oss-120b` ⇄ `qwen3.8-27b` ⇄ `gpt-oss-20b`)**\n"
                    f"🚫 **已自動排除**：「已售出、暫售、已洽談、洽中、預定中、不用再來信、目標品項已售」之文章。\n"
                    f"✅ **共找到 `{len(unsold_items)}` 篇目前確定仍可購買的商品**："
                ),
                color=discord.Color.green() if unsold_items else discord.Color.orange(),
                timestamp=datetime.now(),
            )
            if not chunk:
                embed.add_field(
                    name="目前近期文章皆已售出/洽談中或無相符貼文",
                    value="機器人正持續在背景盯梢中，一旦有賣家新上架將會第一時間推播通知您！",
                    inline=False,
                )
            else:
                start_num = (page_idx - 1) * chunk_size + 1
                for idx, item in enumerate(chunk, start=start_num):
                    det = item.get("detail", {})
                    price_str = det.get("extracted_price") or "請見內文"
                    loc_str = det.get("extracted_location") or "請見內文"
                    q_cnt = det.get("buyer_queue_count", 0)
                    q_badge = (
                        f"🔥 {q_cnt}人留言排/私" if q_cnt > 0 else "✨ 尚無人留言排隊"
                    )
                    ai_model = (det.get("ai_verified_by") or "規則引擎").split("/")[-1]
                    ai_note = (
                        f"\n• 📝 **AI 摘要**：`{det['ai_summary_note']}`"
                        if det.get("ai_summary_note")
                        else ""
                    )

                    embed.add_field(
                        name=f"{idx}. [{item['matched_keyword']}] {item['title'][:80]}",
                        value=(
                            f"• 💰 **目標售價**：`{price_str[:50]}` ｜ 📍 **交易**：`{loc_str[:40]}`\n"
                            f"• 👤 **作者**：`{item['author']}` ｜ 📅 **日期**：`{item['date']}` ｜ {q_badge}"
                            f"{ai_note}\n"
                            f"• 🤖 **審核模型**：`{ai_model}` ｜ 🔗 [點此前往 PTT 查看原文]({item['url']})"
                        ),
                        inline=False,
                    )
            embed.set_footer(
                text=f"{scope_label} | 第 {page_idx}/{len(item_chunks)} 頁 | 今日掃描額度: {used_cnt}/{limit_cnt}"
            )
            pages.append(enforce_embed_limits(embed))

        if push_to_channel and unsold_items:
            target = await bot.delivery.resolve_subscriber_target(sub)
            if target:
                for item in unsold_items[:5]:
                    det = item.get("detail", {})
                    p_embed = build_article_embed(
                        item,
                        [f"[未售出] {item['matched_keyword']}"],
                        det,
                        scope_label=scope_label,
                    )
                    await target.send(
                        embed=p_embed,
                        view=ArticleLinkView(item["url"], board),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    await asyncio.sleep(1.2)

        if len(pages) > 1:
            await interaction.followup.send(
                embed=pages[0],
                view=PaginatedEmbedView(pages, owner_id=interaction.user.id),
            )
        else:
            await interaction.followup.send(embed=pages[0])
    finally:
        bot.scheduler.release_user_slot(uid_str, "scan")


# =====================================================================
# Slash Commands - AI Fuzzy Tracking Management
# =====================================================================
@bot.tree.command(
    name="fuzzy_add",
    description="🧠 新增『AI 模糊追蹤條件』（例如：預算 6500 元以下有保固的 3060TI、台北面交的雙風扇顯卡）",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    description="自然語言描述你想追蹤的條件（AI 會在看到新文章時自動閱讀內文與價格替你判斷）",
    board="目標看板（預設 HardwareSale）",
    category="限定分類（預設只看 [賣/...] 販售文）",
    hint_keywords="選填：快速初篩詞（用逗號分隔，如 3060,6700；留空則檢查該看板所有新販售文）",
    scope="選擇要新增至「個人專屬追蹤(私訊)」或「伺服器共用追蹤」",
)
@app_commands.choices(
    category=[
        app_commands.Choice(name="只看販售文 [賣/...]", value="賣"),
        app_commands.Choice(name="只看徵求文 [徵/...]", value="徵"),
        app_commands.Choice(name="不限分類 (ALL)", value="ALL"),
    ],
    scope=SCOPE_CHOICES,
)
async def cmd_fuzzy_add(
    interaction: discord.Interaction,
    description: str,
    board: str = "HardwareSale",
    category: str = "賣",
    hint_keywords: str = "",
    scope: str = "auto",
):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False)

    if board.lower() in COMMON_BOARDS_MAP:
        board = COMMON_BOARDS_MAP[board.lower()]

    # Fix P0-4: Verify board existence on PTT if not already tracked
    if board.upper() != "ALL" and not bot.storage.is_board_tracked(board):
        valid, canonical_name, v_msg = await bot.crawler.verify_board(board)
        if not valid:
            await interaction.followup.send(f"❌ 看板驗證失敗：{v_msg}")
            return
        board = canonical_name

    ok, rule = bot.storage.add_fuzzy_rule(
        description=description,
        board=board,
        category=category,
        hint_keywords=hint_keywords,
        subscriber_id=sub["id"],
    )
    if not ok:
        await interaction.followup.send(f"⚠️ {rule.get('error', '新增失敗')}")
        return

    embed = discord.Embed(
        title="🧠 成功新增 AI 模糊追蹤規則",
        description=f"**規則編號 `#{rule['id']}`**：\n> {rule['description']}",
        color=discord.Color.from_rgb(155, 89, 182),
        timestamp=datetime.now(),
    )
    embed.add_field(name="👤 歸屬範圍", value=f"`{scope_label}`", inline=True)
    embed.add_field(name="📋 目標看板", value=f"`{rule['board']}`", inline=True)
    embed.add_field(name="🏷️ 限定分類", value=f"`{rule['category']}`", inline=True)
    embed.add_field(
        name="⚡ 快速初篩詞",
        value=f"`{rule['hint_keywords'] or '無 (自動審核所有新文章)'}`",
        inline=False,
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="fuzzy_remove", description="移除指定的 AI 模糊追蹤規則")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    rule="選擇或輸入要刪除的模糊規則編號",
    scope="選擇要操作的追蹤範圍（預設自動判斷）",
)
@app_commands.autocomplete(rule=fuzzy_autocomplete)
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_fuzzy_remove(
    interaction: discord.Interaction, rule: str, scope: str = "auto"
):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return

    removed = bot.storage.remove_fuzzy_rule(rule, subscriber_id=sub["id"])
    if not removed and scope == "auto" and interaction.guild:
        user_sub = bot.storage.get_subscriber("user", str(interaction.user.id))
        if user_sub:
            removed = bot.storage.remove_fuzzy_rule(rule, subscriber_id=user_sub["id"])
            if removed:
                scope_label = f"👤 個人專屬追蹤 ({interaction.user.display_name})"

    if not removed:
        await interaction.response.send_message(
            f"❌ 在 **{scope_label}** 下找不到模糊規則 `{rule}`，請使用 `/fuzzy_list` 查看目前清單。",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        f"🗑️ 已從 **{scope_label}** 移除 AI 模糊追蹤規則 `#{removed['id']}`：「{removed['description']}」"
    )


@bot.tree.command(name="fuzzy_list", description="查看目前設定的所有 AI 模糊追蹤規則")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(scope="選擇要查看「個人專屬追蹤」或「伺服器共用追蹤」")
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_fuzzy_list(interaction: discord.Interaction, scope: str = "auto"):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=False
    )
    if err or not sub:
        await interaction.response.send_message(
            err or "❌ 無法識別身分", ephemeral=True
        )
        return

    rules = bot.storage.get_fuzzy_rules(subscriber_id=sub["id"])
    embed = discord.Embed(
        title=f"🧠 目前啟用的 AI 模糊追蹤規則 — {scope_label}",
        color=discord.Color.from_rgb(155, 89, 182),
        timestamp=datetime.now(),
    )
    if not rules:
        embed.description = (
            "目前尚未設定任何 AI 模糊追蹤規則。\n"
            "👉 您可以使用 `/fuzzy_add` 新增，或直接私訊 / `/ai` 告訴 AI 助手（例如：`/ai 幫我模糊追蹤 6500 元以下的 3060Ti`）！"
        )
    else:
        lines = []
        for r in rules:
            lines.append(
                f"**#{r['id']}** 「{r['description']}」\n"
                f"　📋 看板: `{r['board']}` | 🏷️ 分類: `{r['category']}` | ⚡ 初篩詞: `{r['hint_keywords'] or '全部'}`"
            )
        embed.description = "\n\n".join(lines)[:3900]
    await interaction.response.send_message(embed=enforce_embed_limits(embed))


# =====================================================================
# Slash Commands - Exact Keyword Management
# =====================================================================
@bot.tree.command(
    name="keyword_add",
    description="新增要精確追蹤的關鍵字（支援 -排除字 與 預算上限，例如：3060 -ti -筆電）",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    keyword="要追蹤的關鍵字（空白分隔代表同時包含；加 - 代表排除，例如：3060 -ti -筆電）",
    board="指定套用的看板（預設 HardwareSale）",
    category="限定文章分類（預設只看 [賣/...] 販售文）",
    max_price="選填：最高預算上限（新台幣整數，例如 6500 代表只通知 ≤6500 元的商品）",
    scope="選擇要新增至「個人專屬追蹤(私訊)」或「伺服器共用追蹤」",
)
@app_commands.choices(
    category=[
        app_commands.Choice(name="只看販售文 [賣/...]", value="賣"),
        app_commands.Choice(name="只看徵求文 [徵/...]", value="徵"),
        app_commands.Choice(name="只看交換文 [換/...]", value="換"),
        app_commands.Choice(name="只看情報文 [情報]", value="情報"),
        app_commands.Choice(name="不限分類 (ALL)", value="ALL"),
    ],
    scope=SCOPE_CHOICES,
)
async def cmd_keyword_add(
    interaction: discord.Interaction,
    keyword: str,
    board: str = "HardwareSale",
    category: str = "賣",
    max_price: Optional[app_commands.Range[int, 100, 500000]] = None,
    scope: str = "auto",
):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False)

    if board.upper() != "ALL" and board.lower() in COMMON_BOARDS_MAP:
        board = COMMON_BOARDS_MAP[board.lower()]

    # Fix P0-4: Verify board on PTT before adding if not already tracked
    if board.upper() != "ALL" and not bot.storage.is_board_tracked(board):
        valid, canonical_name, v_msg = await bot.crawler.verify_board(board)
        if not valid:
            await interaction.followup.send(f"❌ 看板驗證失敗：{v_msg}")
            return
        board = canonical_name

    ok, msg = bot.storage.add_keyword(
        keyword=keyword,
        board=board,
        category=category,
        subscriber_id=sub["id"],
        max_price=max_price,
    )
    if not ok:
        await interaction.followup.send(f"⚠️ {msg}")
        return

    push_ch = sub.get("push_channel_id")
    target_desc = (
        f"<#{push_ch}>"
        if push_ch
        else (
            "您的 Discord 私訊 (DM)"
            if sub["scope"] == "user"
            else "尚未設定 (請用 `/channel_set`)"
        )
    )
    total_kws = len(bot.storage.get_keywords(subscriber_id=sub["id"]))

    embed = discord.Embed(
        title="✅ 成功新增精確追蹤關鍵字",
        description=msg,
        color=discord.Color.green(),
        timestamp=datetime.now(),
    )
    embed.add_field(
        name="📢 推播設定",
        value=f"• 歸屬範圍：`{scope_label}`\n• 推播目標：{target_desc}\n• 目前精確關鍵字數：`{total_kws}` 組",
        inline=False,
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(
    name="keyword_edit",
    description="✏️ 修改已設定的精確關鍵字文字或最高預算上限（max_price）",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    keyword_id="選擇或輸入要修改的關鍵字（或編號）",
    new_keyword="選填：更新後的關鍵字文字（不填則維持原文字）",
    max_price="選填：更新後的最高預算上限（填入 0 代表清除預算上限）",
    scope="選擇要操作的追蹤範圍（預設自動判斷）",
)
@app_commands.autocomplete(keyword_id=keyword_autocomplete)
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_keyword_edit(
    interaction: discord.Interaction,
    keyword_id: str,
    new_keyword: Optional[str] = None,
    max_price: Optional[app_commands.Range[int, 0, 500000]] = None,
    scope: str = "auto",
):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return

    clear_price = max_price == 0
    actual_price = None if (max_price is None or max_price == 0) else max_price
    ok, msg = bot.storage.update_keyword(
        keyword_id,
        subscriber_id=sub["id"],
        new_keyword=new_keyword,
        new_max_price=actual_price,
        clear_max_price=clear_price,
    )
    if not ok and scope == "auto" and interaction.guild:
        user_sub = bot.storage.get_subscriber("user", str(interaction.user.id))
        if user_sub:
            ok, msg = bot.storage.update_keyword(
                keyword_id,
                subscriber_id=user_sub["id"],
                new_keyword=new_keyword,
                new_max_price=actual_price,
                clear_max_price=clear_price,
            )
            if ok:
                scope_label = f"👤 個人專屬追蹤 ({interaction.user.display_name})"

    if not ok:
        await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
        return
    await interaction.response.send_message(f"✏️ **{scope_label}**：{msg}")


@bot.tree.command(name="keyword_remove", description="移除已設定的精確追蹤關鍵字")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    keyword="選擇或輸入要刪除的關鍵字（或編號）",
    scope="選擇要操作的追蹤範圍（預設自動判斷）",
)
@app_commands.autocomplete(keyword=keyword_autocomplete)
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_keyword_remove(
    interaction: discord.Interaction, keyword: str, scope: str = "auto"
):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return

    removed = bot.storage.remove_keyword(keyword, subscriber_id=sub["id"])
    if not removed and scope == "auto" and interaction.guild:
        user_sub = bot.storage.get_subscriber("user", str(interaction.user.id))
        if user_sub:
            removed = bot.storage.remove_keyword(keyword, subscriber_id=user_sub["id"])
            if removed:
                scope_label = f"👤 個人專屬追蹤 ({interaction.user.display_name})"

    if not removed:
        await interaction.response.send_message(
            f"❌ 在 **{scope_label}** 下找不到關鍵字或編號 `{keyword}`，請使用 `/keyword_list` 查看目前清單。",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"🗑️ 已從 **{scope_label}** 移除關鍵字：`{removed['keyword']}` (看板: `{removed['board']}`, 分類: `{removed['category']}`)"
    )


@bot.tree.command(
    name="keyword_list", description="查看目前設定的所有精確關鍵字與 AI 模糊規則"
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(scope="選擇要查看「個人專屬追蹤」或「伺服器共用追蹤」")
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_keyword_list(interaction: discord.Interaction, scope: str = "auto"):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=False
    )
    if err or not sub:
        await interaction.response.send_message(
            err or "❌ 無法識別身分", ephemeral=True
        )
        return

    keywords = bot.storage.get_keywords(subscriber_id=sub["id"])
    fuzzy_rules = bot.storage.get_fuzzy_rules(subscriber_id=sub["id"])
    push_mode = sub.get("push_mode", "keyword")
    push_ch = sub.get("push_channel_id")
    enabled_str = "✅ 啟用中" if sub.get("enabled", 1) else "⏸️ 已暫停通知 (`/resume` 可恢復)"
    target_str = (
        f"<#{push_ch}>"
        if push_ch
        else ("Discord 私訊 (DM 直接通知)" if sub["scope"] == "user" else "未設定")
    )
    mode_desc = (
        "僅推播符合條件的新文章"
        if push_mode == "keyword"
        else "推播所有新文章（命中條件高亮）"
    )

    embed = discord.Embed(
        title=f"🔍 追蹤關鍵字與 AI 模糊規則清單 — {scope_label}",
        color=discord.Color.blue(),
        timestamp=datetime.now(),
    )
    embed.add_field(
        name="⚙️ 推播目標與模式",
        value=f"• 推播狀態：{enabled_str}\n• 推播目標：{target_str}\n• 推播模式：`{mode_desc}`",
        inline=False,
    )

    kw_lines = [
        f"**#{kw['id']}** `{kw['keyword']}` — 看板: `{kw['board']}` | 分類: `{kw['category']}`"
        + (f" | 預算: `≤${kw['max_price']}`" if kw.get("max_price") else "")
        for kw in keywords
    ] or ["尚未設定精確關鍵字（可用 `/keyword_add` 或直接私訊 AI 新增）"]
    embed.add_field(
        name=f"🎯 精確關鍵字 ({len(keywords)})",
        value="\n".join(kw_lines)[:1000],
        inline=False,
    )

    fz_lines = [
        f"**#{r['id']}** 「{r['description']}」 (看板: `{r['board']}` | 分類: `{r['category']}`)"
        for r in fuzzy_rules
    ] or ["尚未設定 AI 模糊規則（可用 `/fuzzy_add` 或 `/ai` 新增）"]
    embed.add_field(
        name=f"🧠 AI 模糊追蹤規則 ({len(fuzzy_rules)})",
        value="\n".join(fz_lines)[:1000],
        inline=False,
    )

    await interaction.response.send_message(embed=enforce_embed_limits(embed))


@bot.tree.command(name="keyword_clear", description="清空您目前範圍內的所有精確追蹤關鍵字")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(scope="選擇要清空「個人專屬追蹤」或「伺服器共用追蹤」")
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_keyword_clear(interaction: discord.Interaction, scope: str = "auto"):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return
    count = bot.storage.clear_keywords(subscriber_id=sub["id"])
    await interaction.response.send_message(
        f"🧹 已清空 **{scope_label}** 下的所有精確關鍵字（共移除 `{count}` 組）。"
    )


# =====================================================================
# Slash Commands - Board Management
# =====================================================================
@bot.tree.command(
    name="board_add",
    description="新增要盯梢追蹤的 PTT 看板（例如 nb-shopping、MacShop、DC_Sale）",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    board="PTT 看板英文名稱（或使用 /ai 讓 AI 幫你依主題尋找看板）",
    scope="選擇操作身分範圍（預設自動判斷）",
)
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_board_add(
    interaction: discord.Interaction, board: str, scope: str = "auto"
):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False)

    valid, canonical_name, msg = await bot.crawler.verify_board(board)
    if not valid:
        await interaction.followup.send(f"❌ 無法新增看板：{msg}")
        return

    if not bot.storage.is_board_tracked(canonical_name):
        quota = bot.storage.get_subscriber_quota(sub["id"])
        sub_boards = bot.storage.get_subscriber_distinct_boards(sub["id"])
        if (
            canonical_name.lower() not in sub_boards
            and len(sub_boards) >= quota.get("max_boards", 5)
        ):
            await interaction.followup.send(
                f"⚠️ 您在 **{scope_label}** 追蹤的不同看板數已達上限（`{quota.get('max_boards', 5)}` 個）。"
            )
            return
        if len(bot.storage.get_boards()) >= MAX_GLOBAL_ACTIVE_BOARDS:
            await interaction.followup.send(
                f"⚠️ 全系統活躍盯梢看板數已達安全上限（`{MAX_GLOBAL_ACTIVE_BOARDS}` 個）。"
            )
            return

    added = bot.storage.add_board(canonical_name)
    if not added:
        await interaction.followup.send(
            f"ℹ️ 看板 `{canonical_name}` 已經在系統盯梢清單中了！"
        )
        return

    ok, articles, _ = await bot.crawler.fetch_board_articles(
        canonical_name, fetch_prev_if_few=False, max_pages=1
    )
    if ok:
        bot.storage.mark_articles_seen(articles)
        bot.storage.update_board_status(
            canonical_name, True, f"已完成基準初始化 ({len(articles)} 篇)"
        )

    embed = discord.Embed(
        title="✅ 成功新增追蹤看板",
        description=(
            f"已將 PTT **`{canonical_name}`** 加入背景盯梢清單！\n"
            f"🔗 看板網址：https://www.ptt.cc/bbs/{canonical_name}/index.html\n"
            f"📦 已載入目前版面 `{len(articles)}` 篇文章作為基準，請記得使用 `/keyword_add` 或 `/fuzzy_add` 新增該看板的追蹤條件！"
        ),
        color=discord.Color.green(),
        timestamp=datetime.now(),
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="board_remove", description="停止追蹤指定的 PTT 看板")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    board="選擇或輸入要停止追蹤的看板名稱",
    scope="選擇要清空「個人專屬追蹤」或「伺服器共用追蹤」在該看板的規則",
)
@app_commands.autocomplete(board=board_autocomplete)
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_board_remove(
    interaction: discord.Interaction, board: str, scope: str = "auto"
):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return

    # Remove this subscriber's rules on that board
    for kw in bot.storage.get_keywords(board=board, subscriber_id=sub["id"]):
        if kw["board"].lower() == board.lower():
            bot.storage.remove_keyword(str(kw["id"]), subscriber_id=sub["id"])
    for fz in bot.storage.get_fuzzy_rules(board=board, subscriber_id=sub["id"]):
        if fz["board"].lower() == board.lower():
            bot.storage.remove_fuzzy_rule(str(fz["id"]), subscriber_id=sub["id"])

    if not bot.storage.has_active_rules_for_board(board):
        removed = bot.storage.remove_board(board)
        if not removed:
            await interaction.response.send_message(
                f"❌ 目前並未追蹤看板 `{board}`，請使用 `/board_list` 查看清單。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"🗑️ 已停止追蹤 PTT **`{removed}`** 看板。"
        )
    else:
        await interaction.response.send_message(
            f"🗑️ 已清空 **{scope_label}** 在看板 `{board}` 的追蹤規則（因其他訂閱者仍有設定此看板規則，系統將維持看板背景巡檢）。"
        )


@bot.tree.command(
    name="board_list", description="查看目前盯梢中的所有 PTT 看板與巡檢狀態"
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cmd_board_list(interaction: discord.Interaction):
    boards = bot.storage.get_boards()
    embed = discord.Embed(
        title="📋 目前盯梢追蹤的 PTT 看板清單",
        color=discord.Color.blurple(),
        timestamp=datetime.now(),
    )
    if not boards:
        embed.description = "目前沒有追蹤任何看板。請使用 `/board_add` 或 `/ai` 新增看板。"
    else:
        for b in boards:
            seen_cnt = bot.storage.get_seen_count(b["name"])
            kws = bot.storage.get_keywords(board=b["name"])
            fzs = bot.storage.get_fuzzy_rules(board=b["name"])
            embed.add_field(
                name=f"📌 {b['name']}",
                value=(
                    f"• 狀態：`{b['last_status']}`\n"
                    f"• 全系統綁定條件：精確關鍵字 `{len(kws)}` 組 / AI 模糊規則 `{len(fzs)}` 組\n"
                    f"• 上次檢查：`{b['last_checked']}` | 已索引：`{seen_cnt}` 篇\n"
                    f"• [前往看板網頁](https://www.ptt.cc/bbs/{b['name']}/index.html)"
                ),
                inline=False,
            )
    await interaction.response.send_message(embed=enforce_embed_limits(embed))


# =====================================================================
# Slash Commands - Channel, Mode, Pause/Resume, Search, Personal Account & Admin
# =====================================================================
@bot.tree.command(
    name="channel_set",
    description="設定要接收 PTT 新文章推播的 Discord 頻道（個人追蹤預設直接發送私訊）",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    channel="選擇要推播的文字頻道（若不填則設為目前輸入指令的頻道）",
    scope="選擇要設定「伺服器共用推播頻道」或「個人自訂推播頻道」",
)
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_channel_set(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    scope: str = "auto",
):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return

    if sub["scope"] == "user" and channel is None and interaction.guild is None:
        bot.storage.update_subscriber_channel(sub["id"], None)
        await interaction.response.send_message(
            "✅ 已將您的 **個人專屬追蹤** 設定為直接發送至 **Discord 私訊 (DM)**！"
        )
        return

    target_channel = channel or interaction.channel
    if target_channel is None or not hasattr(target_channel, "id"):
        await interaction.response.send_message(
            "❌ 無法識別目標頻道，請明確指定一個伺服器文字頻道。", ephemeral=True
        )
        return

    ch_guild = getattr(target_channel, "guild", None)
    if ch_guild is None or interaction.guild is None or ch_guild.id != interaction.guild.id:
        await interaction.response.send_message(
            "❌ 目標推播頻道必須屬於目前所在的伺服器文字頻道。", ephemeral=True
        )
        return

    # Prevent unprivileged users from routing personal ('user') notifications into a server channel
    if sub["scope"] == "user" and not has_guild_write_permission(
        interaction.user, ch_guild
    ):
        await interaction.response.send_message(
            "🔒 將個人追蹤通知導向伺服器文字頻道需要該伺服器的 `管理伺服器` 或 `管理頻道` 權限（一般使用者預設透過 Discord 私訊 DM 接收通知）。",
            ephemeral=True,
        )
        return

    bot_member = getattr(ch_guild, "me", None)
    if bot_member and hasattr(target_channel, "permissions_for"):
        perms = target_channel.permissions_for(bot_member)
        if not (perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                f"⚠️ 機器人在 <#{target_channel.id}> 缺少 `發送訊息 (Send Messages)` 或 `嵌入連結 (Embed Links)` 權限，請先調整頻道權限後再試！",
                ephemeral=True,
            )
            return

    bot.storage.update_subscriber_channel(sub["id"], str(target_channel.id))
    await interaction.response.send_message(
        f"✅ 已將 **{scope_label}** 的推播頻道設定為 <#{target_channel.id}> (`{target_channel.id}`)！"
    )


@bot.tree.command(
    name="push_mode",
    description="切換推播模式（僅推播符合條件 vs 推播看板所有新文章）",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    mode="選擇推播模式",
    scope="選擇要設定「個人專屬追蹤」或「伺服器共用追蹤」",
)
@app_commands.choices(
    mode=[
        app_commands.Choice(
            name="🎯 僅推播符合精確關鍵字或 AI 模糊規則的文章（預設）", value="keyword"
        ),
        app_commands.Choice(
            name="📢 推播所有新文章（僅限管理員開啟）", value="all"
        ),
    ],
    scope=SCOPE_CHOICES,
)
async def cmd_push_mode(
    interaction: discord.Interaction, mode: str, scope: str = "auto"
):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return

    if mode == "all" and sub.get("tier") != "owner":
        await interaction.response.send_message(
            "⚠️ 為避免洗版與觸發速率限制，「推播所有新文章 (`all`)」模式僅開放給主伺服器管理員使用。一般追蹤請使用預設的 `keyword` 精準/模糊比對模式！",
            ephemeral=True,
        )
        return

    bot.storage.update_subscriber_mode(sub["id"], mode)
    if mode == "keyword":
        msg = f"🎯 已將 **{scope_label}** 切換為 **「僅推播符合條件的新文章」** 模式！"
    else:
        msg = f"📢 已將 **{scope_label}** 切換為 **「推播所有新文章」** 模式！"
    await interaction.response.send_message(msg)


@bot.tree.command(
    name="pause",
    description="⏸️ 暫停接收目前範圍的推播通知（保留所有已設定的關鍵字與模糊規則）",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(scope="選擇要暫停「個人專屬追蹤」或「伺服器共用追蹤」")
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_pause(interaction: discord.Interaction, scope: str = "auto"):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return
    bot.storage.set_subscriber_enabled(sub["id"], False)
    await interaction.response.send_message(
        f"⏸️ 已暫停 **{scope_label}** 的自動推播通知（您的所有追蹤規則均完整保留，隨時可輸入 `/resume` 恢復通知）。"
    )


@bot.tree.command(
    name="resume",
    description="▶️ 恢復接收目前範圍的推播通知",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(scope="選擇要恢復「個人專屬追蹤」或「伺服器共用追蹤」")
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_resume(interaction: discord.Interaction, scope: str = "auto"):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=True
    )
    if err or not sub:
        await interaction.response.send_message(err or "❌ 權限不足", ephemeral=True)
        return
    bot.storage.set_subscriber_enabled(sub["id"], True)
    await interaction.response.send_message(
        f"▶️ 已恢復 **{scope_label}** 的自動推播通知！"
    )


_SEARCH_COOLDOWN_MAP: Dict[str, float] = {}
SEARCH_COOLDOWN_SEC = 10.0


@bot.tree.command(name="search", description="使用 PTT 原生搜尋引擎搜尋看板文章")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    keyword="要搜尋的關鍵字（例如：3060TI、6700XT）",
    board="要搜尋的 PTT 看板（預設 HardwareSale）",
    pages="搜尋頁數（1~2 頁，預設 1 頁）",
)
async def cmd_search(
    interaction: discord.Interaction,
    keyword: str,
    board: str = "HardwareSale",
    pages: app_commands.Range[int, 1, 2] = 1,
):
    uid_str = str(interaction.user.id)
    now = time.time()
    last_search = _SEARCH_COOLDOWN_MAP.get(uid_str, 0.0)
    if now - last_search < SEARCH_COOLDOWN_SEC:
        rem = max(1, int(SEARCH_COOLDOWN_SEC - (now - last_search)))
        await interaction.response.send_message(
            f"⏳ 搜尋冷卻中：為防範 PTT 爬蟲連線過載，請等待 {rem} 秒後再試！",
            ephemeral=True,
        )
        return

    if not bot.scheduler.acquire_user_slot(uid_str, "search"):
        await interaction.response.send_message(
            "⚠️ 您的另一個搜尋任務仍在進行中，請稍候片刻完成後再試！",
            ephemeral=True,
        )
        return

    _SEARCH_COOLDOWN_MAP[uid_str] = now
    try:
        await interaction.response.defer(ephemeral=False)

        if board.lower() in COMMON_BOARDS_MAP:
            board = COMMON_BOARDS_MAP[board.lower()]

        ok, articles, status_msg = await bot.crawler.search_board_by_ptt_query(
            board, keyword, max_pages=pages
        )
        if not ok:
            await interaction.followup.send(f"❌ 搜尋看板 `{board}` 失敗：{status_msg}")
            return

        embed = discord.Embed(
            title=f"🔎 PTT 【{board}】搜尋結果：{keyword}",
            description=f"共找到 **`{len(articles)}`** 篇相關文章（若要自動過濾已售出文章，請使用 `/scan_unsold`）：",
            color=discord.Color.gold() if articles else discord.Color.light_grey(),
            timestamp=datetime.now(),
        )

        if not articles:
            embed.add_field(
                name="沒有找到符合的文章",
                value="您可以透過 `/keyword_add` 或 `/fuzzy_add` 將條件加入背景盯梢！",
                inline=False,
            )
        else:
            lines = []
            for idx, art in enumerate(articles[:12], start=1):
                lines.append(
                    f"**{idx}.** [{art['title']}]({art['url']})\n"
                    f"　👤 `{art['author']}` | 📅 `{art['date']}` | 推文: `{art['nrec']}`"
                )
            embed.add_field(
                name=f"搜尋結果（顯示前 {min(12, len(articles))} 篇）",
                value="\n".join(lines)[:1000],
                inline=False,
            )

        await interaction.followup.send(embed=enforce_embed_limits(embed))
    except Exception as e:
        logger.error(f"/search 指令執行異常: {e}", exc_info=True)
        await interaction.followup.send("❌ 處理時發生系統錯誤，已記錄於內部日誌。若持續發生請洽管理員。")
    finally:
        bot.scheduler.release_user_slot(uid_str, "search")


@bot.tree.command(
    name="me",
    description="👤 查看您個人的專屬追蹤設定、私訊推播狀態與今日 AI 額度",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cmd_me(interaction: discord.Interaction):
    uid_str = str(interaction.user.id)
    user_sub = bot.storage.get_or_create_subscriber(
        "user", uid_str, display_name=interaction.user.display_name
    )
    u_kws = bot.storage.get_keywords(subscriber_id=user_sub["id"])
    u_fzs = bot.storage.get_fuzzy_rules(subscriber_id=user_sub["id"])
    quota = bot.storage.get_subscriber_quota(user_sub["id"])
    used_ai = bot.storage.get_daily_usage(user_sub["id"], "ai_chat")
    used_scan = bot.storage.get_daily_usage(user_sub["id"], "scan")

    if not user_sub.get("enabled", 1):
        dm_status = "⏸️ 已暫停推播（輸入 `/resume` 可隨時恢復）"
    elif user_sub.get("dm_failed"):
        dm_status = "⚠️ 曾發生私訊失敗（請確認已開啟允許伺服器成員私訊，或直接私訊機器人一次即可恢復）"
    elif user_sub.get("push_channel_id"):
        dm_status = f"📢 自訂頻道：<#{user_sub['push_channel_id']}>"
    else:
        dm_status = "✅ 正常（命中時直接私訊通知您）"

    embed = discord.Embed(
        title=f"👤 {interaction.user.display_name} 的個人專屬追蹤面板",
        description="您可以**直接私訊 (DM) 機器人**用自然語言對話，或在任何地方使用 Slash 指令並將 `scope` 設為 `👤 個人專屬追蹤`！",
        color=discord.Color.from_rgb(46, 204, 113),
        timestamp=datetime.now(),
    )
    embed.add_field(name="📬 私訊推播狀態", value=dm_status, inline=False)
    embed.add_field(
        name="📊 今日配額使用量",
        value=(
            f"• 身分等級：`{user_sub.get('tier', 'friend')}`\n"
            f"• AI 對話次數：`{used_ai} / {quota['daily_ai_chats']}` 次\n"
            f"• 歷史未售出掃描：`{used_scan} / {quota['daily_scans']}` 次\n"
            f"• 關鍵字上限：`{len(u_kws)} / {quota['max_keywords']}` 組 ｜ 模糊規則：`{len(u_fzs)} / {quota['max_fuzzy_rules']}` 組"
        ),
        inline=False,
    )

    kw_str = (
        "\n".join(
            f"• **#{k['id']}** `{k['keyword']}` ({k['board']}/{k['category']}"
            + (f", ≤${k['max_price']}" if k.get("max_price") else "")
            + ")"
            for k in u_kws
        )
        or "尚未設定個人關鍵字（直接私訊機器人說：`幫我追蹤 4070` 即可新增！）"
    )
    embed.add_field(
        name=f"🎯 您的個人精確關鍵字 ({len(u_kws)})",
        value=kw_str[:1000],
        inline=False,
    )

    fz_str = (
        "\n".join(
            f"• **#{r['id']}** 「{r['description']}」 ({r['board']}/{r['category']})"
            for r in u_fzs
        )
        or "尚未設定個人 AI 模糊規則"
    )
    embed.add_field(
        name=f"🧠 您的個人 AI 模糊規則 ({len(u_fzs)})",
        value=fz_str[:1000],
        inline=False,
    )
    await interaction.response.send_message(
        embed=enforce_embed_limits(embed), ephemeral=True
    )


@bot.tree.command(
    name="forget_me",
    description="🗑️ 一鍵清除您個人的所有追蹤規則、推播紀錄與 AI 對話記憶（隱私清除）",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cmd_forget_me(interaction: discord.Interaction):
    uid_str = str(interaction.user.id)
    removed = bot.storage.forget_subscriber("user", uid_str)
    if removed:
        await interaction.response.send_message(
            "🗑️ 已完整刪除您個人的所有追蹤關鍵字、AI 模糊規則、推播紀錄與對話記憶。未來若想再次使用，隨時私訊機器人即可重新開始！",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "ℹ️ 目前系統中沒有您的個人追蹤資料。", ephemeral=True
        )


@bot.tree.command(
    name="admin",
    description="🛡️ 系統管理員專用指令（檢視全系統訂閱戶、調整配額等級、管理黑名單）",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    action="選擇管理操作",
    target_id="目標 Discord User ID 或 Guild ID（調整等級或黑名單時必填）",
    tier="設定配額等級（free / friend / owner）",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="📊 檢視全系統訂閱戶與統計 (stats)", value="stats"),
        app_commands.Choice(name="⭐ 調整訂閱戶配額等級 (set_tier)", value="set_tier"),
        app_commands.Choice(name="🚫 加入黑名單 (blacklist)", value="blacklist"),
        app_commands.Choice(name="✅ 解除黑名單 (unblacklist)", value="unblacklist"),
    ],
    tier=[
        app_commands.Choice(name="friend (朋友個人追蹤配額)", value="friend"),
        app_commands.Choice(name="free (一般外部伺服器配額)", value="free"),
        app_commands.Choice(name="owner (管理員無限制配額)", value="owner"),
    ],
)
async def cmd_admin(
    interaction: discord.Interaction,
    action: str,
    target_id: Optional[str] = None,
    tier: str = "friend",
):
    is_authorized = is_bot_owner(interaction.user.id) or (
        interaction.guild is not None
        and str(interaction.guild.id) == str(DEFAULT_GUILD_ID)
        and has_guild_write_permission(interaction.user, interaction.guild)
    )
    if not is_authorized:
        await interaction.response.send_message(
            "🚫 此指令僅限系統管理員執行。", ephemeral=True
        )
        return

    if action == "stats":
        subs = bot.storage.get_all_enabled_subscribers()
        all_kws = bot.storage.get_keywords()
        all_fzs = bot.storage.get_fuzzy_rules()
        boards = bot.storage.get_boards()
        lines = [
            f"• **Sub#{s['id']}** [`{s['scope']}`/`{s['tier']}`] {s.get('display_name') or s['discord_id']} (`{s['discord_id']}`)"
            for s in subs[:25]
        ]
        embed = discord.Embed(
            title="🛡️ 系統管理員總覽 (Multi-Tenant Stats)",
            description=(
                f"• 活躍訂閱戶：`{len(subs)}` 組\n"
                f"• 全系統精確關鍵字：`{len(all_kws)}` 組 ｜ 模糊規則：`{len(all_fzs)}` 組\n"
                f"• 監控看板數：`{len(boards)} / {MAX_GLOBAL_ACTIVE_BOARDS}` 個"
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="👥 訂閱戶清單 (前 25 筆)",
            value="\n".join(lines)[:1000] or "無",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if not target_id or not target_id.strip():
        await interaction.response.send_message(
            "⚠️ 請提供 `target_id`（Discord User ID 或 Guild ID）。", ephemeral=True
        )
        return

    tid = target_id.strip()
    if action == "set_tier":
        sub = bot.storage.get_subscriber("user", tid) or bot.storage.get_subscriber(
            "guild", tid
        )
        if not sub:
            await interaction.response.send_message(
                f"❌ 找不到對應 `{tid}` 的訂閱戶。", ephemeral=True
            )
            return
        bot.storage.set_subscriber_tier(sub["scope"], sub["discord_id"], tier)
        await interaction.response.send_message(
            f"✅ 已將訂閱戶 `#{sub['id']}` (`{tid}`) 的配額等級調整為 **`{tier}`**！",
            ephemeral=True,
        )
    elif action == "blacklist":
        bot.storage.set_blacklist(tid, True)
        await interaction.response.send_message(
            f"🚫 已將 `{tid}` 加入黑名單。", ephemeral=True
        )
    elif action == "unblacklist":
        bot.storage.set_blacklist(tid, False)
        await interaction.response.send_message(
            f"✅ 已將 `{tid}` 從黑名單移除。", ephemeral=True
        )


@bot.tree.command(
    name="help",
    description="📖 查看 PTT 智慧追蹤機器人的完整使用教學（支援朋友私訊、多伺服器與 AI 語意追蹤）",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 PTT 二手硬體 & 看板智慧追蹤機器人 — 使用指南",
        description=(
            "本機器人支援 **多使用者個人私訊追蹤** 與 **多伺服器共用頻道推播**，並內建 **Groq 多模型交替 AI 語意審核**，自動幫您過濾「已售出、已洽談、不用再來信」的文章！"
        ),
        color=discord.Color.from_rgb(52, 152, 219),
        timestamp=datetime.now(),
    )
    embed.add_field(
        name="💬 方式一：直接私訊 (DM) 機器人開始個人追蹤（最推薦朋友使用！）",
        value=(
            "• 點擊機器人頭像 → **「傳送訊息 (Message)」**，直接用自然語言跟它說話即可：\n"
            "  👉 `幫我追蹤 HardwareSale 板的 4070S 跟 7800XT`\n"
            "  👉 `幫我追蹤 6500 元以下、有保固的 3060Ti`\n"
            "  👉 `幫我掃描目前版上還沒賣掉的 3060TI 跟 6700XT`\n"
            "  👉 `幫我找賣二手相機或蘋果產品的 PTT 看板並加入追蹤`\n"
            "• 在私訊中設定的規則全部屬於您**個人專屬**，新商品上架時會直接**私訊通知您**，不會打擾伺服器頻道！"
        ),
        inline=False,
    )
    embed.add_field(
        name="🏠 方式二：邀請至朋友的伺服器共用推播",
        value=(
            "• 邀請機器人進入伺服器後，管理員可使用 `/channel_set` 設定推播頻道。\n"
            "• 在伺服器頻道中輸入 `@PTT機器人` 或 `/keyword_add`、`/fuzzy_add`，預設會管理該**伺服器的共用追蹤清單**。\n"
            "• 若在伺服器內想操作自己的個人私訊追蹤，只要在指令的 `scope` 選擇 `👤 個人專屬追蹤` 即可！"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚡ 進階精確關鍵字語法（排除字 `-` 與預算上限 `max_price`）",
        value=(
            "• **空白分隔（同時包含）**：`台北 3060TI`（標題須同時有台北與 3060TI）\n"
            "• **負向排除字（`-` 開頭）**：`3060 -ti -筆電 -礦`（追蹤純 3060，排除 3060Ti、筆電與礦卡）\n"
            "• **價格上限過濾**：使用 `/keyword_add` 或 `/keyword_edit` 時可設定 `max_price: 6000`，超過預算自動不推播！"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛠️ 常用 Slash 指令一覽",
        value=(
            "• `/ai`：用自然語言請 AI 助手設定規則、找看板或查行情\n"
            "• `/scan_unsold`：掃描過去符合條件且**尚未售出/未洽談**的商品\n"
            "• `/keyword_add` / `/keyword_edit` / `/keyword_remove` / `/keyword_list`：管理精確關鍵字\n"
            "• `/fuzzy_add` / `/fuzzy_remove` / `/fuzzy_list`：管理 AI 模糊語意規則\n"
            "• `/pause` / `/resume`：暫停或恢復通知 ｜ `/me`：查看個人面板 ｜ `/forget_me`：清除個人資料\n"
            "• `/status`：查看機器人巡檢狀態與 Groq 多模型佇列負載"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=enforce_embed_limits(embed))


@bot.tree.command(
    name="check_now", description="手動立即觸發一次設定看板的精準巡檢與推播"
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cmd_check_now(interaction: discord.Interaction):
    uid_str = str(interaction.user.id)
    if bot.storage.is_blacklisted(uid_str):
        await interaction.response.send_message("🚫 您的帳號已被限制使用。", ephemeral=True)
        return

    now = time.time()
    if not is_bot_owner(uid_str) and now - bot._last_manual_check_ts < 300:
        remain = int(300 - (now - bot._last_manual_check_ts))
        await interaction.response.send_message(
            f"⏳ 系統剛執行過手動巡檢，請於 `{remain}` 秒後再試（背景自動巡檢仍持續運作中）。",
            ephemeral=True,
        )
        return

    bot._last_manual_check_ts = now
    await interaction.response.defer(ephemeral=False)
    summary = await bot.run_single_poll_cycle()
    await interaction.followup.send(
        f"✅ 手動巡檢完成！\n"
        f"• 實際掃描看板數：`{summary['checked_boards']}`\n"
        f"• 發現新文章數：`{summary['new_articles']}`\n"
        f"• 本次推播數：`{summary['pushed']}`"
    )


@bot.tree.command(
    name="status",
    description="查看機器人完整狀態、精確關鍵字、AI 模糊規則與 Groq 佇列負載",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(scope="選擇要查看哪個範圍的追蹤規則（預設自動判斷）")
@app_commands.choices(scope=SCOPE_CHOICES)
async def cmd_status(interaction: discord.Interaction, scope: str = "auto"):
    sub, scope_label, err = resolve_interaction_subscriber(
        interaction, scope_choice=scope, require_write_perm=False
    )
    if err or not sub:
        await interaction.response.send_message(
            err or "❌ 無法識別身分", ephemeral=True
        )
        return

    boards = bot.storage.get_boards()
    keywords = bot.storage.get_keywords(subscriber_id=sub["id"])
    fuzzy_rules = bot.storage.get_fuzzy_rules(subscriber_id=sub["id"])
    all_subs = bot.storage.get_all_enabled_subscribers()
    push_ch_id = sub.get("push_channel_id")
    push_mode = sub.get("push_mode", "keyword")
    total_pushed = bot.storage.get_setting("total_pushed", "0")
    total_seen = bot.storage.get_seen_count()
    in_cd, cd_remain = bot.crawler.is_in_cooldown()
    q_status = bot.scheduler.get_queue_status()

    target_str = (
        f"<#{push_ch_id}> (`{push_ch_id}`)"
        if push_ch_id
        else ("Discord 私訊 (DM)" if sub["scope"] == "user" else "未設定")
    )

    embed = discord.Embed(
        title=f"📊 PTT 智慧追蹤機器人運作狀態 — {scope_label}",
        color=discord.Color.orange() if in_cd else discord.Color.green(),
        timestamp=datetime.now(),
    )
    embed.add_field(
        name="📢 當前推播目標",
        value=target_str,
        inline=True,
    )
    model_parts = []
    for m_short, m_info in q_status["models"].items():
        model_parts.append(
            f"`{m_short}`: {m_info['used']}次 (餘{m_info['rem_tok']}tok)"
        )
    embed.add_field(
        name=f"🧠 Groq 排程佇列 (排隊:{q_status['queue_size']})",
        value="\n".join(model_parts) or "就緒",
        inline=True,
    )
    embed.add_field(
        name="🛡️ 防封鎖 & 多租戶",
        value=(
            f"⚠️ 冷卻中 ({cd_remain}s)"
            if in_cd
            else f"✅ 正常 (請求 {bot.crawler.total_requests} 次 / 訂閱戶 {len(all_subs)} 組)"
        ),
        inline=True,
    )

    board_lines = [
        f"• **`{b['name']}`** — {b['last_status']} (上次檢查: `{b['last_checked']}`)"
        for b in boards
    ] or ["無追蹤看板"]
    embed.add_field(
        name=f"📋 系統精準盯梢看板 ({len(boards)})",
        value="\n".join(board_lines)[:1000],
        inline=False,
    )

    kw_lines = [
        f"`#{k['id']} {k['keyword']}` ({k['board']}/{k['category']}"
        + (f"/≤${k['max_price']}" if k.get("max_price") else "")
        + ")"
        for k in keywords
    ] or ["無"]
    embed.add_field(
        name=f"🎯 當前範圍精確關鍵字 ({len(keywords)})",
        value=" 、 ".join(kw_lines)[:1000],
        inline=False,
    )

    fz_lines = [
        f"• `#{r['id']}` {r['description']} (`{r['board']}`/`{r['category']}`)"
        for r in fuzzy_rules
    ] or ["無（可使用 `/fuzzy_add` 或私訊 AI 新增）"]
    embed.add_field(
        name=f"🧠 當前範圍 AI 模糊追蹤規則 ({len(fuzzy_rules)})",
        value="\n".join(fz_lines)[:1000],
        inline=False,
    )

    next_t = (
        bot.next_poll_time.strftime("%H:%M:%S") if bot.next_poll_time else "即將執行"
    )
    embed.add_field(
        name="📈 系統統計資訊",
        value=(
            f"• 推播模式：`{'僅推播符合條件文章' if push_mode == 'keyword' else '推播所有新文章'}`\n"
            f"• 輪詢間隔：`{POLL_MIN} ~ {POLL_MAX}` 秒隨機抖動（下次檢查：`{next_t}`）\n"
            f"• 資料庫已索引文章：`{total_seen}` 篇 | 累計已推播：`{total_pushed}` 則"
        ),
        inline=False,
    )

    await interaction.response.send_message(embed=enforce_embed_limits(embed))


def main() -> None:
    for sensitive_file in (os.path.join(BASE_DIR, ".env"), DB_PATH):
        if os.path.exists(sensitive_file):
            try:
                os.chmod(sensitive_file, 0o600)
            except Exception:
                pass
    if not DISCORD_TOKEN:
        raise RuntimeError("請在 .env 檔案中設定 DISCORD_TOKEN")
    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
