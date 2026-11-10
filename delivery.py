import abc
import asyncio
import logging
import random
from datetime import datetime
from typing import Any, Dict, List, Optional

import discord

from config import MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER, OWNER_USER_ID
from embeds import (
    ArticleLinkView,
    build_article_embed,
    enforce_embed_limits,
    update_existing_embed_for_sold,
)
from ptt_crawler import CRAWL_P2_SOLD_CHECK, PTTCrawler
from storage import BotStorage, compute_dedupe_hash

logger = logging.getLogger("delivery")

# F15: states that may still change within the follow-up window (not final like SOLD / DELETED)
TEMPORARY_STATES = ("RESERVED", "NO_MAIL")


class BaseNotificationProvider(abc.ABC):
    """
    多平台通知供應商抽象基底類別。
    所有支援的通訊與推播平台 (Discord, Telegram, Email) 均需實作此介面。
    """

    platform_name: str = "base"

    @abc.abstractmethod
    async def deliver(
        self,
        subscriber: Dict[str, Any],
        items: List[Dict[str, Any]],
    ) -> int:
        """將已過濾之命中文章推播至指定訂閱者。回傳成功發送數。"""
        pass

    @abc.abstractmethod
    async def update_sold(
        self,
        subscriber: Dict[str, Any],
        delivery_record: Dict[str, Any],
        detail: Dict[str, Any],
    ) -> bool:
        """當文章於 PTT 被標記為已售出/預定時更新訊息。回傳是否更新成功。"""
        pass


class DiscordNotificationProvider(BaseNotificationProvider):
    """
    Discord 原生通知供應商：
    負責 Discord 頻道與私訊解析、Embed 卡片渲染、按鈕組件封裝與訊息原處修改。
    """

    platform_name: str = "discord"

    def __init__(self, bot: discord.Client, storage: BotStorage):
        self.bot = bot
        self.storage = storage

    async def resolve_target(
        self, sub: Dict[str, Any]
    ) -> Optional[discord.abc.Messageable]:
        sub_id = sub["id"]
        scope = sub.get("scope", "guild")
        push_ch_id = sub.get("push_channel_id")

        if push_ch_id and str(push_ch_id).isdigit():
            cid = int(push_ch_id)
            ch = self.bot.get_channel(cid)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(cid)
                except Exception as e:
                    logger.warning(f"無法取得訂閱者 #{sub_id} 的推播頻道 ({cid}): {e}")
                    return None
            if scope == "guild":
                ch_guild = getattr(ch, "guild", None)
                if ch_guild is None or str(ch_guild.id) != str(sub.get("discord_id", "")):
                    logger.warning(
                        f"安全攔截：訂閱者 #{sub_id} (Guild {sub.get('discord_id')}) 的推播頻道 ({cid}) 不屬於該伺服器"
                    )
                    return None
            elif scope == "user":
                # Supplemental Rule #4: If a user routed personal notifications to a guild channel
                # and later lost guild management permissions (or left the guild), stop pushing to that channel.
                ch_guild = getattr(ch, "guild", None)
                uid_str = str(sub.get("discord_id") or "")
                if ch_guild is not None and uid_str.isdigit():
                    if not (OWNER_USER_ID and uid_str == str(OWNER_USER_ID)):
                        uid_int = int(uid_str)
                        is_owner = getattr(ch_guild, "owner_id", None) == uid_int
                        if not is_owner and hasattr(ch_guild, "get_member"):
                            member = ch_guild.get_member(uid_int)
                            if member is None:
                                logger.warning(
                                    f"安全攔截：個人訂閱者 #{sub_id} ({uid_str}) 已不在伺服器 {getattr(ch_guild, 'id', '')}，拒絕推播至頻道 ({cid})"
                                )
                                return None
                            perms = getattr(member, "guild_permissions", None)
                            if perms is not None and not (
                                getattr(perms, "administrator", False)
                                or getattr(perms, "manage_guild", False)
                                or getattr(perms, "manage_channels", False)
                            ):
                                logger.warning(
                                    f"安全攔截：個人訂閱者 #{sub_id} ({uid_str}) 於伺服器之管理權限已被收回，拒絕推播至頻道 ({cid})"
                                )
                                return None
            return ch

        if scope == "user":
            if sub.get("dm_failed"):
                return None
            uid_str = str(sub.get("discord_id") or sub.get("target_id") or "")
            if not uid_str.isdigit():
                return None
            uid = int(uid_str)
            user = self.bot.get_user(uid)
            if user is None:
                try:
                    user = await self.bot.fetch_user(uid)
                except Exception as e:
                    logger.warning(f"無法取得使用者 ({uid}) 以發送私訊: {e}")
                    return None
            try:
                return user.dm_channel or await user.create_dm()
            except discord.Forbidden:
                logger.warning(f"使用者 {uid} 關閉了陌生/伺服器成員私訊，標記 dm_failed=1")
                self.storage.mark_subscriber_dm_failed(sub_id, True)
                return None
            except Exception as e:
                logger.warning(f"建立私訊頻道 ({uid}) 失敗: {e}")
                return None

        return None

    async def deliver(
        self,
        subscriber: Dict[str, Any],
        items: List[Dict[str, Any]],
    ) -> int:
        target = await self.resolve_target(subscriber)
        if target is None:
            return 0

        sub_id = subscriber["id"]
        scope = subscriber.get("scope", "guild")
        scope_label = (
            f"[個人追蹤] {subscriber.get('display_name') or subscriber.get('discord_id')}"
            if scope == "user"
            else f"[伺服器追蹤] {subscriber.get('display_name') or '共用'}"
        )

        pushed = 0
        primary_slice = items[:MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER]
        overflow_slice = items[MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER:]

        for it in primary_slice:
            art = it["article"]
            matched_kws = it.get("matched_keywords") or []
            detail = it.get("detail")
            fuzzy_hits = it.get("fuzzy_matches") or []

            embed = build_article_embed(
                art,
                matched_kws,
                detail,
                fuzzy_hits,
                scope_label=scope_label,
            )
            view = ArticleLinkView(art["url"], art["board"])
            try:
                msg = await target.send(
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                ch_id = getattr(target, "id", None) or subscriber.get("push_channel_id")
                kw_to_record = (
                    it.get("matched_keyword")
                    or (matched_kws[0] if matched_kws else "")
                )
                self.storage.record_delivery(
                    subscriber_id=sub_id,
                    article=art,
                    channel_id=str(ch_id) if ch_id else None,
                    message_id=str(msg.id) if msg else None,
                    matched_keyword=kw_to_record,
                )
                self.storage.increment_pushed_count(1)
                pushed += 1
                await asyncio.sleep(random.uniform(1.2, 2.0))
            except discord.Forbidden:
                if scope == "user" and not subscriber.get("push_channel_id"):
                    logger.warning(f"發送私訊給訂閱者 #{sub_id} 遭拒 (Forbidden)，標記 dm_failed=1")
                    self.storage.mark_subscriber_dm_failed(sub_id, True)
                else:
                    logger.warning(f"發送頻道訊息給訂閱者 #{sub_id} 權限不足 (Forbidden)")
                break
            except Exception as e:
                logger.error(f"推播訊息給訂閱者 #{sub_id} 失敗: {e}")
                break

        if overflow_slice and pushed > 0:
            for chunk_start in range(0, len(overflow_slice), 10):
                chunk = overflow_slice[chunk_start : chunk_start + 10]
                lines = []
                for idx, it in enumerate(chunk, start=chunk_start + 1):
                    art = it["article"]
                    det = it.get("detail") or {}
                    pr = det.get("extracted_price") or "見內文"
                    lines.append(f"**{idx}.** [{art['title'][:65]}]({art['url']}) — `{pr}`")
                ov_embed = discord.Embed(
                    title=f"同時命中的其他新文章 ({len(overflow_slice)} 篇)",
                    description="\n".join(lines),
                    color=discord.Color.gold(),
                    timestamp=datetime.now(),
                )
                ov_embed.set_footer(text=f"{scope_label} | 防洗版摘要卡片")
                try:
                    await target.send(
                        embed=enforce_embed_limits(ov_embed),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    for it in chunk:
                        self.storage.record_delivery(
                            subscriber_id=sub_id,
                            article=it["article"],
                            channel_id=None,
                            message_id=None,
                        )
                except Exception:
                    break

        return pushed

    async def _resolve_delivery_channel(
        self, subscriber: Dict[str, Any], channel_id: int
    ) -> Optional[discord.abc.Messageable]:
        """
        F12: Resolve the channel a card was ORIGINALLY delivered to (from the delivery record),
        not the subscriber's current push channel, so changing /setup never orphans old cards.
        - discord.NotFound / Forbidden propagate (caller marks edit_failed: permanent).
        - Other errors return None (transient; caller retries later).
        """
        ch = self.bot.get_channel(channel_id)
        if ch is None:
            # DM channels are often not cached; the subscriber's DM channel may match
            if subscriber.get("scope") == "user" and not subscriber.get("push_channel_id"):
                dm = await self.resolve_target(subscriber)
                if dm is not None and getattr(dm, "id", None) == channel_id:
                    return dm
            try:
                ch = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                raise
            except Exception as e:
                logger.debug(f"暫時無法取得原推播頻道 ({channel_id}): {e}")
                return None
        if subscriber.get("scope", "guild") == "guild":
            ch_guild = getattr(ch, "guild", None)
            if ch_guild is None or str(ch_guild.id) != str(subscriber.get("discord_id", "")):
                logger.warning(
                    f"安全攔截：投遞紀錄頻道 ({channel_id}) 不屬於訂閱者 #{subscriber.get('id')} 之伺服器"
                )
                return None
        return ch

    async def update_sold(
        self,
        subscriber: Dict[str, Any],
        delivery_record: Dict[str, Any],
        detail: Dict[str, Any],
    ) -> bool:
        ch_id = delivery_record.get("channel_id")
        msg_id = delivery_record.get("message_id")
        if not ch_id or not msg_id or not str(ch_id).isdigit() or not str(msg_id).isdigit():
            return False

        try:
            target = await self._resolve_delivery_channel(subscriber, int(ch_id))
            if target is None:
                return False
            msg = await target.fetch_message(int(msg_id))
            existing_embeds = getattr(msg, "embeds", None)
            if (
                existing_embeds
                and isinstance(existing_embeds, (list, tuple))
                and len(existing_embeds) > 0
                and isinstance(existing_embeds[0], discord.Embed)
            ):
                sold_embed = update_existing_embed_for_sold(
                    existing_embeds[0],
                    detail=detail,
                    board=delivery_record.get("board", "HardwareSale"),
                    fallback_title=delivery_record.get("title", "已售出商品"),
                )
            else:
                art_stub = {
                    "url": delivery_record.get("article_url", ""),
                    "board": delivery_record.get("board", "HardwareSale"),
                    "title": delivery_record.get("title", "已售出商品"),
                    "author": delivery_record.get("author", "未知"),
                    "date": "",
                    "category": "賣",
                    "nrec": "-",
                }
                mkw = delivery_record.get("matched_keyword")
                sold_embed = build_article_embed(
                    art_stub,
                    matched_keywords=[mkw] if mkw else [],
                    detail=detail,
                    is_sold_update=True,
                )
            await msg.edit(embed=sold_embed)
            return True
        except (discord.NotFound, discord.Forbidden) as e:
            logger.warning(
                f"更新 Discord 已售出訊息 ({msg_id}) 遭拒或已刪除，標記 edit_failed=1 不再重試: {e}"
            )
            d_id = delivery_record.get("id")
            if d_id is not None and self.storage and hasattr(self.storage, "mark_delivery_edit_failed"):
                self.storage.mark_delivery_edit_failed(int(d_id))
            return False
        except Exception as e:
            logger.debug(f"更新 Discord 已售出訊息 ({msg_id}) 略過: {e}")
            return False


class DeliveryDispatcher:
    """
    解耦通知分發器：
    根據訂閱者的 platform 屬性，將文章推播與售出追蹤更新導向至已註冊之 NotificationProvider。
    統一在分發層執行跨平台的 3 天去重檢驗 (compute_dedupe_hash)。
    """

    def __init__(self, storage: Optional[BotStorage] = None):
        self.storage = storage
        self._providers: Dict[str, BaseNotificationProvider] = {}

    def register_provider(
        self, platform: str, provider: BaseNotificationProvider
    ) -> None:
        key = platform.lower().strip()
        self._providers[key] = provider
        logger.info(f"已註冊通知供應商: {key} -> {provider.__class__.__name__}")

    def get_provider(self, platform: str) -> Optional[BaseNotificationProvider]:
        return self._providers.get(platform.lower().strip())

    def unregister_provider(self, platform: str) -> Optional[BaseNotificationProvider]:
        return self._providers.pop(platform.lower().strip(), None)

    @property
    def registered_platforms(self) -> List[str]:
        return list(self._providers.keys())

    async def dispatch(
        self,
        subscriber: Dict[str, Any],
        items: List[Dict[str, Any]],
    ) -> int:
        if not items:
            return 0

        platform = str(subscriber.get("platform") or "discord").lower().strip()
        provider = self._providers.get(platform)
        if not provider:
            logger.warning(
                f"無法分發通知：未註冊的推播平台 '{platform}' (訂閱者 #{subscriber.get('id')})"
            )
            return 0

        sub_id = subscriber.get("id")
        filtered_items = []
        for it in items:
            art = it.get("article", {})
            d_hash = compute_dedupe_hash(
                art.get("board", ""), art.get("author", ""), art.get("title", "")
            )
            if (
                self.storage
                and sub_id is not None
                and self.storage.is_duplicate_delivery(sub_id, art.get("url", ""), d_hash)
            ):
                logger.info(
                    f"[Dedupe] 略過重複上架/已推播文章 (Sub#{sub_id}, 平台={platform}): {art.get('title')}"
                )
                continue
            filtered_items.append(it)

        if not filtered_items:
            return 0

        try:
            return await provider.deliver(subscriber, filtered_items)
        except Exception as e:
            logger.error(f"推播分發失敗 (平台={platform}, Sub#{sub_id}): {e}", exc_info=True)
            return 0

    async def dispatch_sold_update(
        self,
        subscriber: Dict[str, Any],
        delivery_record: Dict[str, Any],
        detail: Dict[str, Any],
    ) -> bool:
        platform = str(subscriber.get("platform") or "discord").lower().strip()
        provider = self._providers.get(platform)
        if not provider:
            return False

        try:
            return await provider.update_sold(subscriber, delivery_record, detail)
        except Exception as e:
            logger.debug(f"售出狀態更新分發略過 (平台={platform}): {e}")
            return False

    async def close(self) -> None:
        """優雅關閉所有已註冊供應商持有的非同步資源 (如 aiohttp session)"""
        for platform_name, provider in list(self._providers.items()):
            if hasattr(provider, "close") and callable(provider.close):
                try:
                    res = provider.close()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    logger.debug(f"[警告] 關閉推播供應商 ({platform_name}) 失敗: {e}")


class DeliveryManager:
    """
    Rate-limited, multi-subscriber notification manager supporting Discord, Telegram, and Email,
    with 3-day repost deduplication and sold-out follow-up editing.

    Now powered by DeliveryDispatcher and BaseNotificationProvider for extensible multi-platform routing.
    """

    def __init__(
        self,
        bot: Optional[discord.Client] = None,
        storage: Optional[BotStorage] = None,
        crawler: Optional[PTTCrawler] = None,
        dispatcher: Optional[DeliveryDispatcher] = None,
        auto_register_providers: bool = True,
        telegram_provider: Optional[BaseNotificationProvider] = None,
        email_provider: Optional[BaseNotificationProvider] = None,
    ):
        self.bot = bot
        self.storage = storage
        self.crawler = crawler

        if dispatcher is not None:
            self.dispatcher = dispatcher
        else:
            self.dispatcher = DeliveryDispatcher(storage=self.storage)

        self.discord_provider: Optional[DiscordNotificationProvider] = None
        self.telegram_provider: Optional[BaseNotificationProvider] = telegram_provider
        self.email_provider: Optional[BaseNotificationProvider] = email_provider

        if auto_register_providers:
            self._auto_register_providers()

    def _auto_register_providers(self) -> None:
        """
        根據 config.py 開關與憑證自動載入與註冊多平台 Provider。
        若外部模組尚未安裝或未啟用，則優雅降級略過。
        """
        import config

        # 1. Discord Provider
        if self.discord_provider is None and self.bot is not None and self.storage is not None:
            enable_discord = getattr(config, "ENABLE_DISCORD", True)
            if enable_discord:
                self.discord_provider = DiscordNotificationProvider(
                    bot=self.bot,
                    storage=self.storage,
                )
                self.dispatcher.register_provider("discord", self.discord_provider)

        # 2. Telegram Provider
        if self.telegram_provider is not None:
            self.dispatcher.register_provider("telegram", self.telegram_provider)
        else:
            enable_tg = getattr(config, "ENABLE_TELEGRAM", False)
            tg_token = getattr(config, "TELEGRAM_BOT_TOKEN", "").strip()
            if enable_tg and tg_token:
                try:
                    from telegram_bot import TelegramNotificationProvider

                    self.telegram_provider = TelegramNotificationProvider(
                        token=tg_token,
                        storage=self.storage,
                    )
                    self.dispatcher.register_provider("telegram", self.telegram_provider)
                    logger.info("[資訊] 已自動掛載 TelegramNotificationProvider")
                except ImportError as e:
                    logger.warning(f"[警告] 無法載入 telegram_bot 模組: {e}")
                except Exception as e:
                    logger.error(
                        f"[錯誤] 自動註冊 TelegramNotificationProvider 失敗: {e}",
                        exc_info=True,
                    )

        # 3. Email Provider
        if self.email_provider is not None:
            self.dispatcher.register_provider("email", self.email_provider)
        else:
            enable_em = getattr(config, "ENABLE_EMAIL", False)
            smtp_host = getattr(config, "SMTP_HOST", "").strip()
            if enable_em and smtp_host:
                try:
                    from email_notifier import EmailNotificationProvider

                    self.email_provider = EmailNotificationProvider(
                        host=smtp_host,
                        port=getattr(config, "SMTP_PORT", 587),
                        user=getattr(config, "SMTP_USER", "").strip(),
                        password=getattr(config, "SMTP_PASSWORD", "").strip(),
                        use_tls=getattr(config, "SMTP_USE_TLS", True),
                        from_addr=getattr(config, "EMAIL_FROM", "").strip()
                        or getattr(config, "SMTP_USER", "").strip(),
                        from_name=getattr(
                            config, "EMAIL_FROM_NAME", "PTT 智慧追蹤機器人"
                        ).strip(),
                        default_to=getattr(config, "EMAIL_TO", "").strip(),
                        storage=self.storage,
                    )
                    self.dispatcher.register_provider("email", self.email_provider)
                    logger.info("[資訊] 已自動掛載 EmailNotificationProvider")
                except ImportError as e:
                    logger.warning(f"[警告] 無法載入 email_notifier 模組: {e}")
                except Exception as e:
                    logger.error(
                        f"[錯誤] 自動註冊 EmailNotificationProvider 失敗: {e}",
                        exc_info=True,
                    )

    def register_provider(self, platform: str, provider: BaseNotificationProvider) -> None:
        """手動註冊或覆寫平台供應商"""
        plat = platform.lower().strip()
        if plat == "discord" and isinstance(provider, DiscordNotificationProvider):
            self.discord_provider = provider
        elif plat == "telegram":
            self.telegram_provider = provider
        elif plat == "email":
            self.email_provider = provider
        self.dispatcher.register_provider(plat, provider)

    async def close(self) -> None:
        """關閉分發器與所有已註冊的供應商資源"""
        if self.dispatcher:
            await self.dispatcher.close()

    async def resolve_subscriber_target(
        self, sub: Dict[str, Any]
    ) -> Optional[discord.abc.Messageable]:
        if self.discord_provider is not None:
            return await self.discord_provider.resolve_target(sub)
        return None

    async def deliver_subscriber_matches(
        self,
        sub: Dict[str, Any],
        items: List[Dict[str, Any]],
    ) -> int:
        return await self.dispatcher.dispatch(sub, items)

    async def check_sold_deliveries_once(self, max_checks: int = 2) -> int:
        if not self.storage or not self.crawler:
            return 0

        recent = self.storage.get_recent_unmarked_deliveries(
            max_age_hours=18, limit=max_checks * 2
        )
        if not recent:
            return 0

        by_url: Dict[str, List[Dict]] = {}
        for row in recent:
            by_url.setdefault(row["article_url"], []).append(row)

        updated_count = 0
        checked_urls = 0
        for url, deliveries in by_url.items():
            if checked_urls >= max_checks:
                break
            in_cd, _ = self.crawler.is_in_cooldown()
            if in_cd:
                break

            checked_urls += 1
            sample = deliveries[0]
            base_detail = await self.crawler.fetch_article_full_detail(
                url,
                article_title=sample.get("title", ""),
                article_author=sample.get("author", ""),
                force_refresh=True,
                priority=CRAWL_P2_SOLD_CHECK,
            )

            fetch_status = base_detail.get("fetch_status", 200)
            is_deleted_404 = bool(base_detail.get("deleted") or fetch_status == 404)

            # Transient HTTP failure (e.g., timeout / 5xx / cooldown): rotate URL to back of queue
            if not is_deleted_404 and fetch_status != 200 and not base_detail.get("is_sold"):
                self.storage.touch_delivery_sold_check(url)
                continue

            for d_row in deliveries:
                d_id = d_row.get("id")
                target_ref = d_id if d_id is not None else url

                if is_deleted_404:
                    item_detail = dict(base_detail)
                    item_detail["is_sold"] = True
                    item_detail["deleted"] = True
                    item_detail["sold_state"] = "DELETED"
                    item_detail["sold_reason"] = (
                        base_detail.get("sold_reason") or "原文已刪除或不存在 (404)"
                    )
                else:
                    kw = d_row.get("matched_keyword") or ""
                    item_detail = PTTCrawler.apply_keyword_view(base_detail, kw)

                prev_state = str(d_row.get("sold_state") or "").upper()
                is_closed = bool(item_detail.get("is_sold") or item_detail.get("deleted"))

                if not is_closed:
                    # F15: a temporarily held item (RESERVED / NO_MAIL) is back on sale -> restore card
                    if prev_state in TEMPORARY_STATES:
                        new_state = "AVAILABLE"
                        item_detail = dict(item_detail)
                        item_detail["sold_state"] = "AVAILABLE"
                        item_detail["is_sold"] = False
                    else:
                        self.storage.touch_delivery_sold_check(target_ref)
                        continue
                else:
                    new_state = (
                        item_detail.get("sold_state")
                        or ("DELETED" if is_deleted_404 else "SOLD")
                    )
                    if new_state == prev_state:
                        # Already shown in this temporary state; keep watching for a final outcome
                        self.storage.touch_delivery_sold_check(target_ref)
                        continue

                # F15: only SOLD / DELETED are final; temporary states keep the 18h follow-up alive
                is_final = new_state not in TEMPORARY_STATES and new_state != "AVAILABLE"

                def _save_state() -> None:
                    if is_final:
                        self.storage.mark_delivery_sold(target_ref, sold_state=new_state)
                    elif isinstance(target_ref, int):
                        self.storage.set_delivery_state(target_ref, new_state)
                    else:
                        self.storage.touch_delivery_sold_check(target_ref)

                sub = self.storage.get_subscriber_by_id(d_row["subscriber_id"])
                if not sub:
                    self.storage.mark_delivery_sold(target_ref, sold_state=new_state)
                    continue

                platform = str(sub.get("platform") or "discord").lower().strip()
                if platform == "email":
                    # Supplemental Rule #3: Email is non-editable (N/A); record state without retry
                    _save_state()
                    continue

                ok = await self.dispatcher.dispatch_sold_update(sub, d_row, item_detail)
                if ok:
                    _save_state()
                    updated_count += 1
                else:
                    self.storage.touch_delivery_sold_check(target_ref)

        return updated_count
