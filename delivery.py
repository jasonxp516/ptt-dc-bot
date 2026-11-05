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


class DeliveryManager:
    """
    Rate-limited, multi-subscriber notification dispatcher supporting both Guild Channels
    and Personal Direct Messages (DMs), with 3-day repost deduplication and sold-out follow-up editing.
    """

    def __init__(self, bot: discord.Client, storage: BotStorage, crawler: PTTCrawler):
        self.bot = bot
        self.storage = storage
        self.crawler = crawler

    async def resolve_subscriber_target(
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
            uid_str = str(sub.get("discord_id", ""))
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

    async def deliver_subscriber_matches(
        self,
        sub: Dict[str, Any],
        items: List[Dict[str, Any]],
    ) -> int:
        """
        Deliver matched articles to a single subscriber.
        Each item in `items` has keys:
          - article: Dict
          - matched_keywords: List[str]
          - detail: Optional[Dict]
          - fuzzy_matches: List[Dict]
        Returns number of pushed messages.
        """
        if not items:
            return 0

        sub_id = sub["id"]
        scope = sub.get("scope", "guild")
        scope_label = (
            f"👤 個人追蹤 ({sub.get('display_name') or sub.get('discord_id')})"
            if scope == "user"
            else f"🏠 伺服器追蹤 ({sub.get('display_name') or '共用'})"
        )

        # Filter out 3-day duplicate reposts for this subscriber
        filtered_items = []
        for it in items:
            art = it["article"]
            d_hash = compute_dedupe_hash(
                art.get("board", ""), art.get("author", ""), art.get("title", "")
            )
            if self.storage.is_duplicate_delivery(sub_id, art["url"], d_hash):
                logger.info(
                    f"[Dedupe] 略過重複上架/已推播文章 (Sub#{sub_id}): {art['title']}"
                )
                continue
            filtered_items.append(it)

        if not filtered_items:
            return 0

        target = await self.resolve_subscriber_target(sub)
        if target is None:
            return 0

        pushed = 0
        primary_slice = filtered_items[:MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER]
        overflow_slice = filtered_items[MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER:]

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
                ch_id = getattr(target, "id", None) or sub.get("push_channel_id")
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
                if scope == "user" and not sub.get("push_channel_id"):
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
                lines.append(f"**{idx}.** [{art['title'][:65]}]({art['url']}) — 💰 `{pr}`")
            ov_embed = discord.Embed(
                title=f"📦 另外還有 {len(overflow_slice)} 篇同時命中的新文章",
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

    async def check_sold_deliveries_once(self, max_checks: int = 2) -> int:
        """
        D1 Follow-up Sold Update:
        Periodically re-checks a couple of recently delivered articles (<18h old) using the rule engine
        (zero AI token cost). If an item is now marked sold/reserved on PTT, edits the original Discord message
        with a strikethrough '🔴【已售出/已結案】' badge!
        """
        recent = self.storage.get_recent_unmarked_deliveries(max_age_hours=18, limit=max_checks * 2)
        if not recent:
            return 0

        # Group by article_url so we only fetch each PTT URL once
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

            # Mark sold in DB first so we don't re-check it
            self.storage.mark_delivery_sold(url)
            art_stub = {
                "url": url,
                "board": sample.get("board", "HardwareSale"),
                "title": sample.get("title", "已售出商品"),
                "author": sample.get("author", "未知"),
                "date": "",
                "category": "賣",
                "nrec": "-",
            }

            for d_row in deliveries:
                ch_id = d_row.get("channel_id")
                msg_id = d_row.get("message_id")
                if not ch_id or not msg_id or not str(ch_id).isdigit() or not str(msg_id).isdigit():
                    continue
                try:
                    sub = self.storage.get_subscriber_by_id(d_row["subscriber_id"])
                    target = await self.resolve_subscriber_target(sub) if sub else None
                    if target is None:
                        continue
                    msg = await target.fetch_message(int(msg_id))
                    sold_embed = build_article_embed(
                        art_stub,
                        matched_keywords=[],
                        detail=detail,
                        is_sold_update=True,
                    )
                    await msg.edit(embed=sold_embed)
                    updated_count += 1
                except Exception as e:
                    logger.debug(f"更新已售出訊息 ({msg_id}) 略過: {e}")

        return updated_count
