from datetime import datetime
from typing import Dict, List, Optional

import discord


class ArticleLinkView(discord.ui.View):
    def __init__(self, url: str, board: str):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label=f"前往 PTT {board} 查看原文",
                url=url,
                style=discord.ButtonStyle.link,
                emoji="🔗",
            )
        )


class PaginatedEmbedView(discord.ui.View):
    """
    Interactive prev/next button pagination when results span multiple pages.
    Includes `owner_id` check so other users in a shared channel cannot hijack pagination.
    """

    def __init__(
        self,
        pages: List[discord.Embed],
        timeout: float = 300.0,
        owner_id: Optional[int] = None,
    ):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.current_idx = 0
        self.owner_id = owner_id
        self._update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "ℹ️ 這張分頁卡片是由其他成員發起的查詢，請自行輸入 `/scan_unsold` 查看您的專屬結果！",
                ephemeral=True,
            )
            return False
        return True

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = self.current_idx <= 0
        self.next_btn.disabled = self.current_idx >= len(self.pages) - 1
        self.page_indicator.label = f"{self.current_idx + 1} / {len(self.pages)}"

    @discord.ui.button(label="⬅️ 上一頁", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_idx > 0:
            self.current_idx -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_idx], view=self)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.gray, disabled=True)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="下一頁 ➡️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_idx < len(self.pages) - 1:
            self.current_idx += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_idx], view=self)


def enforce_embed_limits(embed: discord.Embed) -> discord.Embed:
    """
    Ensure a Discord Embed strictly obeys Discord API limits:
    - Title <= 256 chars
    - Description <= 4000 chars
    - Each field name <= 256 chars, value <= 1024 chars, max 25 fields
    - Total characters across title + description + fields + footer + author <= 5800 chars
    """
    if embed.title and len(embed.title) > 250:
        embed.title = embed.title[:247] + "..."
    if embed.description and len(embed.description) > 3800:
        embed.description = embed.description[:3795] + "\n..."

    total_len = len(embed.title or "") + len(embed.description or "")
    if embed.footer and embed.footer.text:
        total_len += len(embed.footer.text)
    if embed.author and embed.author.name:
        total_len += len(embed.author.name)

    kept_fields = []
    for f in embed.fields[:25]:
        name = (f.name or "-")[:250]
        val = (f.value or "-")[:1000]
        if total_len + len(name) + len(val) > 5700:
            remain = 5700 - total_len - len(name)
            if remain > 60:
                kept_fields.append((name, val[: remain - 10] + " ...", f.inline))
            break
        kept_fields.append((name, val, f.inline))
        total_len += len(name) + len(val)

    embed.clear_fields()
    for name, val, inline in kept_fields:
        embed.add_field(name=name, value=val, inline=inline)

    return embed


def build_article_embed(
    article: Dict,
    matched_keywords: List[str],
    detail: Optional[Dict] = None,
    fuzzy_matches: Optional[List[Dict[str, str]]] = None,
    scope_label: Optional[str] = None,
    is_sold_update: bool = False,
) -> discord.Embed:
    title = article["title"]
    cat = article.get("category", "")

    if is_sold_update:
        color = discord.Color.from_rgb(127, 140, 141)
        display_title = f"🔴【已售出/已結案】~~{title[:210]}~~"
        author_header = f"PTT 【{article['board']}】商品狀態更新（已售出或停止收信）"
    else:
        display_title = title[:250]
        author_header = f"PTT 【{article['board']}】新上架通知（尚未售出）"
        if fuzzy_matches:
            color = discord.Color.from_rgb(155, 89, 182)
        elif "賣" in cat or "售" in cat:
            color = discord.Color.from_rgb(46, 204, 113)
        elif "徵" in cat:
            color = discord.Color.from_rgb(52, 152, 219)
        elif matched_keywords:
            color = discord.Color.from_rgb(230, 126, 34)
        else:
            color = discord.Color.from_rgb(149, 165, 166)

    embed = discord.Embed(
        title=display_title,
        url=article["url"],
        color=color,
        timestamp=datetime.now(),
    )
    embed.set_author(
        name=author_header,
        url=f"https://www.ptt.cc/bbs/{article['board']}/index.html",
    )

    if matched_keywords:
        kw_str = " 、 ".join(f"`{k}`" for k in matched_keywords)
        embed.add_field(name="🎯 命中精確關鍵字", value=kw_str[:1000], inline=False)

    if fuzzy_matches:
        f_lines = [
            f"• **{m['rule_id']}** `{m['description']}`\n  👉 *{m['reason']}*"
            for m in fuzzy_matches
        ]
        embed.add_field(
            name="🧠 AI 模糊追蹤命中",
            value="\n".join(f_lines)[:1000],
            inline=False,
        )

    embed.add_field(name="👤 作者", value=f"`{article.get('author', '未知')}`", inline=True)
    embed.add_field(name="🏷️ 分類", value=f"`{cat or '一般'}`", inline=True)
    embed.add_field(
        name="📅 日期 / 推文",
        value=f"`{article.get('date', '-')}` (推: `{article.get('nrec', '0')}`)",
        inline=True,
    )

    if detail:
        if detail.get("extracted_price"):
            embed.add_field(
                name="💰 欲售價格",
                value=f"**`{detail['extracted_price'][:80]}`**",
                inline=True,
            )
        if detail.get("extracted_location"):
            embed.add_field(
                name="📍 交易方式/地區",
                value=f"`{detail['extracted_location'][:80]}`",
                inline=True,
            )
        q_cnt = detail.get("buyer_queue_count", 0)
        q_str = f"🔥 已有 `{q_cnt}` 人留言排/私訊" if q_cnt > 0 else "✨ 尚無人留言排隊"
        embed.add_field(name="👥 買家熱度", value=q_str, inline=True)

        if not is_sold_update and detail.get("ai_summary_note"):
            embed.add_field(
                name="🤖 AI 品況/語意摘要",
                value=f"`{detail['ai_summary_note'][:180]}`",
                inline=False,
            )

        if is_sold_update and detail.get("sold_reason"):
            embed.add_field(
                name="🔴 結案原因",
                value=f"`{detail['sold_reason'][:200]}`",
                inline=False,
            )

        snippet = detail.get("snippet")
        if snippet:
            embed.add_field(
                name="📄 內文重點預覽",
                value=f"```text\n{snippet[:850]}\n```",
                inline=False,
            )
        footer_parts = []
        if detail.get("post_time"):
            footer_parts.append(f"發文: {detail['post_time']}")
        if scope_label:
            footer_parts.append(scope_label)
        if detail.get("ai_verified_by"):
            footer_parts.append(f"AI審核: {detail['ai_verified_by'].split('/')[-1]}")
        else:
            footer_parts.append("PTT 智慧監控")
        embed.set_footer(text=" | ".join(footer_parts)[:200])

        if detail.get("image_url"):
            embed.set_thumbnail(url=detail["image_url"])
    else:
        footer_txt = f"{scope_label} | PTT 二手硬體智慧追蹤" if scope_label else "PTT 二手硬體智慧追蹤"
        embed.set_footer(text=footer_txt)

    return enforce_embed_limits(embed)
