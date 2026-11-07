import abc
import asyncio
import logging
import random
from datetime import datetime
from typing import Any, Dict, List, Optional

import discord

from config import MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER
from embeds import ArticleLinkView, build_article_embed, enforce_embed_limits
from ptt_crawler import PTTCrawler
from storage import BotStorage, compute_dedupe_hash

logger = logging.getLogger("delivery")


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
                self.storage.record_delivery(
                    subscriber_id=sub_id,
                    article=art,
                    channel_id=str(ch_id) if ch_id else None,
                    message_id=str(msg.id) if msg else None,
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
            lines = []
            for idx, it in enumerate(overflow_slice[:10], start=1):
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
                for it in overflow_slice:
                    self.storage.record_delivery(
                        subscriber_id=sub_id,
                        article=it["article"],
                        channel_id=None,
                        message_id=None,
                    )
            except Exception:
                pass

        return pushed

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
            target = await self.resolve_target(subscriber)
            if target is None:
                return False
            msg = await target.fetch_message(int(msg_id))
            art_stub = {
                "url": delivery_record.get("article_url", ""),
                "board": delivery_record.get("board", "HardwareSale"),
                "title": delivery_record.get("title", "已售出商品"),
                "author": delivery_record.get("author", "未知"),
                "date": "",
                "category": "賣",
                "nrec": "-",
            }
            sold_embed = build_article_embed(
                art_stub,
                matched_keywords=[],
                detail=detail,
                is_sold_update=True,
            )
            await msg.edit(embed=sold_embed)
            return True
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


class DeliveryManager:
    """
    Rate-limited, multi-subscriber notification manager supporting both Guild Channels
    and Personal Direct Messages (DMs), with 3-day repost deduplication and sold-out follow-up editing.

    Now powered by DeliveryDispatcher and BaseNotificationProvider for extensible multi-platform routing.
    """

    def __init__(
        self,
        bot: Optional[discord.Client] = None,
        storage: Optional[BotStorage] = None,
        crawler: Optional[PTTCrawler] = None,
        dispatcher: Optional[DeliveryDispatcher] = None,
    ):
        self.bot = bot
        self.storage = storage
        self.crawler = crawler

        if dispatcher is not None:
            self.dispatcher = dispatcher
        else:
            self.dispatcher = DeliveryDispatcher(storage=self.storage)

        if self.bot is not None and self.storage is not None:
            self.discord_provider = DiscordNotificationProvider(
                bot=self.bot,
                storage=self.storage,
            )
            self.dispatcher.register_provider("discord", self.discord_provider)
        else:
            self.discord_provider = None

    def register_provider(self, platform: str, provider: BaseNotificationProvider) -> None:
        """捷徑：在底層分發器上註冊其他平台供應商"""
        self.dispatcher.register_provider(platform, provider)

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
            detail = await self.crawler.fetch_article_full_detail(
                url,
                article_title=sample.get("title", ""),
                article_author=sample.get("author", ""),
                force_refresh=True,
            )
            if not detail.get("is_sold"):
                self.storage.touch_delivery_sold_check(url)
                continue

            self.storage.mark_delivery_sold(url)

            for d_row in deliveries:
                sub = self.storage.get_subscriber_by_id(d_row["subscriber_id"])
                if not sub:
                    continue
                ok = await self.dispatcher.dispatch_sold_update(sub, d_row, detail)
                if ok:
                    updated_count += 1

        return updated_count
