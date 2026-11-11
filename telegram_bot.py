"""
PTT 智慧追蹤機器人 - Telegram 機器人與推播通知模組 (telegram_bot.py)

本模組遵循零外部第三方 Telegram 相依原則，全數使用 aiohttp 與 Python 標準函式庫實作：
- TelegramAPIClient: 輕量級非同步 Telegram Bot REST API 客戶端
- format_article_card: 安全 HTML 實體轉義與 4,000 字元上限截斷防護
- TelegramNotificationProvider: 實作 BaseNotificationProvider 介面 (deliver 與 update_sold)
- TelegramBot: 長輪詢 (Long Polling) 迴圈、雙向文字斜線指令與行內按鈕刪除處理
"""

import asyncio
import html
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp

import time

from boards import COMMON_BOARDS_MAP, resolve_board_alias
from config import MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER
from delivery import BaseNotificationProvider
from storage import BotStorage, parse_budget

logger = logging.getLogger("telegram_bot")


class TelegramAPIClient:
    """
    輕量級 Telegram Bot API 客戶端 (基於 aiohttp)：
    支援 getMe, getUpdates, sendMessage, editMessageText, answerCallbackQuery, setMyCommands。
    內建 HTTP 429 指數退避重試與 HTTP 403 阻擋偵測。
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.telegram.org",
        session: Optional[aiohttp.ClientSession] = None,
    ):
        self.token = token.strip()
        self.api_base = f"{base_url.rstrip('/')}/bot{self.token}"
        self._session = session
        self._owns_session = False
        # R05: error code of the most recent getUpdates call (None = success)
        self.last_error_code: Optional[int] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=45, sock_read=35)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def request(
        self, method: str, payload: Optional[Dict[str, Any]] = None, max_retries: int = 3
    ) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.api_base}/{method}"
        retry_count = 0

        while retry_count <= max_retries:
            try:
                async with session.post(url, json=payload or {}) as resp:
                    try:
                        data = await resp.json(content_type=None)
                    except ValueError:
                        # N5: non-JSON body (e.g. 502 HTML from an upstream proxy) is transient
                        data = None
                    if not isinstance(data, dict):
                        logger.warning(
                            f"[警告] Telegram API 回應非 JSON ({method}, HTTP {resp.status})，稍後重試"
                        )
                        retry_count += 1
                        if retry_count <= max_retries:
                            await asyncio.sleep(2.0 * retry_count)
                            continue
                        return {
                            "ok": False,
                            "error_code": resp.status or 502,
                            "description": "Non-JSON response",
                        }

                    # 1. 成功回傳
                    if resp.status == 200 and data.get("ok"):
                        return data

                    # 2. HTTP 429 速率限制退避
                    if resp.status == 429:
                        params = data.get("parameters") or {}
                        retry_after = int(params.get("retry_after", 3))
                        logger.warning(
                            f"[警告] Telegram API 429 限速，等待 {retry_after} 秒後重試..."
                        )
                        await asyncio.sleep(retry_after + 1.0)
                        retry_count += 1
                        continue

                    # 3. HTTP 403 用戶封鎖或剔除
                    if resp.status == 403:
                        logger.warning(
                            f"[警告] Telegram API 403 拒絕存取 (用戶可能已封鎖 Bot): {data.get('description')}"
                        )
                        return {
                            "ok": False,
                            "error_code": 403,
                            "description": data.get("description", "Forbidden"),
                        }

                    # 4. 其他 API 錯誤 (如 400 Bad Request)
                    logger.debug(f"Telegram API 呼叫失敗 ({method}): {data}")
                    return data

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"[警告] Telegram 網路連線例外 ({method}): {e}")
                retry_count += 1
                if retry_count <= max_retries:
                    await asyncio.sleep(2.0 * retry_count)
                else:
                    return {"ok": False, "error_code": 500, "description": str(e)}

        return {"ok": False, "error_code": 504, "description": "Max retries exceeded"}

    async def get_me(self) -> Dict[str, Any]:
        return await self.request("getMe")

    async def get_updates(
        self, offset: Optional[int] = None, timeout: int = 25, limit: int = 100
    ) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"timeout": timeout, "limit": limit}
        if offset is not None:
            payload["offset"] = offset
        data = await self.request("getUpdates", payload)
        if data.get("ok"):
            self.last_error_code = None
            return data.get("result", [])
        try:
            self.last_error_code = int(data.get("error_code") or 500)
        except (TypeError, ValueError):
            self.last_error_code = 500
        self.last_error_description = str(data.get("description") or "")
        return []

    async def send_message(
        self,
        chat_id: Union[int, str],
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
        disable_web_page_preview: bool = False,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.request("sendMessage", payload)

    async def edit_message_text(
        self,
        chat_id: Union[int, str],
        message_id: int,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.request("editMessageText", payload)

    async def answer_callback_query(
        self, callback_query_id: str, text: Optional[str] = None, show_alert: bool = False
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text
        return await self.request("answerCallbackQuery", payload)

    async def get_chat_member(
        self, chat_id: Union[int, str], user_id: Union[int, str]
    ) -> Dict[str, Any]:
        return await self.request(
            "getChatMember", {"chat_id": chat_id, "user_id": user_id}, max_retries=1
        )

    async def set_my_commands(self, commands: List[Dict[str, str]]) -> bool:
        data = await self.request("setMyCommands", {"commands": commands})
        return bool(data.get("ok"))


def escape_tg(text: Any) -> str:
    """對 HTML 特殊字元進行實體轉義"""
    return html.escape(str(text or ""), quote=True)


def format_article_card(
    article: Dict[str, Any],
    matched_keywords: Optional[List[str]] = None,
    detail: Optional[Dict[str, Any]] = None,
    is_sold: bool = False,
    scope_label: str = "",
) -> str:
    """
    產生符合 Telegram HTML 規範的文章通知卡片。
    總長度嚴格控制於 4,000 字元以內，杜絕標籤溢出。
    """
    board = escape_tg(article.get("board", "HardwareSale"))
    title_raw = article.get("title", "無標題")
    author = escape_tg(article.get("author", "未知"))
    url = article.get("url", "")

    detail = detail or {}
    price_val = detail.get("extracted_price")
    price_display = escape_tg(price_val) if price_val else "見內文"
    category = escape_tg(article.get("category", "賣"))
    ai_note = detail.get("ai_summary_note") or detail.get("ai_reason")

    sold_state = detail.get("sold_state") or ("DELETED" if detail.get("deleted") else "SOLD")

    if is_sold:
        if sold_state == "DELETED":
            header = f"<s>[{board}] {escape_tg(title_raw)}</s>\n<b>[原文已刪除]</b>"
        elif sold_state == "RESERVED":
            header = f"<b>[{board}] {escape_tg(title_raw)}</b>\n<b>[洽談中 / 預定中]</b>"
        elif sold_state == "NO_MAIL":
            header = f"<b>[{board}] {escape_tg(title_raw)}</b>\n<b>[暫勿來信]</b>"
        else:
            header = f"<s>[{board}] {escape_tg(title_raw)}</s>\n<b>[已售出 / 結束]</b>"
    else:
        header = f"<b>[{board}] {escape_tg(title_raw)}</b>"

    lines = [header]
    if scope_label:
        lines.append(f"<b>追蹤範圍</b>: {escape_tg(scope_label)}")
    lines.append(f"<b>看板</b>: #{board}  |  <b>分類</b>: [{category}]")
    lines.append(f"<b>作者</b>: {author}")

    if is_sold:
        if sold_state == "RESERVED":
            lines.append(f"<b>欲售價格</b>: <code>{price_display}</code> <b>[洽談中]</b>")
        elif sold_state == "NO_MAIL":
            lines.append(f"<b>欲售價格</b>: <code>{price_display}</code> <b>[暫勿來信]</b>")
        elif sold_state == "DELETED":
            lines.append(f"<b>欲售價格</b>: <s>{price_display}</s> <b>[原文已刪除]</b>")
        else:
            lines.append(f"<b>欲售價格</b>: <s>{price_display}</s> <b>[已售出]</b>")
    else:
        lines.append(f"<b>欲售價格</b>: <code>{price_display}</code>")

    if matched_keywords:
        kw_tags = " ".join([f"<code>{escape_tg(k)}</code>" for k in matched_keywords])
        lines.append(f"<b>命中規則</b>: {kw_tags}")

    if ai_note:
        lines.append(f"<i>[AI 摘要] {escape_tg(ai_note)}</i>")

    card_text = "\n".join(lines)

    # F17: Truncate on the RAW (unescaped) snippet, then escape, so the limit can never cut
    # through an HTML entity (&amp;) or tag. Header/fields are bounded separately.
    read_more = f'\n... <a href="{escape_tg(url)}">[點此閱讀全文]</a>'
    if len(card_text) > TG_CARD_LIMIT - len(read_more):
        card_text = _truncate_raw_then_escape(
            "\n".join(lines), TG_CARD_LIMIT - len(read_more)
        )

    snippet = (detail.get("content_snippet") or detail.get("snippet") or "").strip()
    if snippet:
        prefix = "\n\n<b>內文重點摘錄</b>:\n<blockquote>"
        suffix = "</blockquote>"
        budget = TG_CARD_LIMIT - len(card_text) - len(prefix) - len(suffix)
        if budget > 0:
            esc = escape_tg(snippet)
            if len(esc) <= budget:
                card_text += f"{prefix}{esc}{suffix}"
            else:
                budget -= len(read_more)
                raw_cut = _fit_raw_to_escaped_budget(snippet, budget)
                if raw_cut:
                    card_text += f"{prefix}{escape_tg(raw_cut)}{suffix}{read_more}"
                else:
                    card_text += read_more
        else:
            card_text += read_more

    return card_text


TG_CARD_LIMIT = 4000


def _fit_raw_to_escaped_budget(raw: str, budget: int) -> str:
    """Longest prefix of `raw` whose HTML-escaped form fits within `budget` characters."""
    if budget <= 0:
        return ""
    out_len = 0
    end = 0
    for ch in raw:
        n = len(escape_tg(ch))
        if out_len + n > budget:
            break
        out_len += n
        end += 1
    return raw[:end]


def _truncate_raw_then_escape(card_html: str, limit: int) -> str:
    """
    Fallback for extremely long header/field sections (e.g. hundreds of matched rules): cut at a
    line boundary so no tag or entity is split; every line built above is self-contained HTML.
    """
    out: List[str] = []
    total = 0
    for ln in card_html.split("\n"):
        add = len(ln) + (1 if out else 0)
        if total + add > limit:
            break
        out.append(ln)
        total += add
    return "\n".join(out)


def _plain_text_card(article: Dict[str, Any], detail: Optional[Dict[str, Any]] = None) -> str:
    """F17: Minimal plain-text fallback card (no parse_mode), bounded to the platform limit."""
    detail = detail or {}
    parts = [
        f"[{article.get('board', '')}] {article.get('title', '')}",
        f"作者: {article.get('author', '')}",
        f"欲售價格: {detail.get('extracted_price') or '見內文'}",
        str(article.get("url", "")),
    ]
    return "\n".join(parts)[:TG_CARD_LIMIT]


_TG_CATEGORY_WORDS = {"賣", "售", "徵", "買", "收", "換", "all"}


def parse_keyword_add_args(
    args: List[str],
) -> Tuple[str, str, str, Optional[int], str]:
    """
    N2: Parse `/keyword_add` arguments -> (board, category, keyword, max_price, error).
      /keyword_add 3060Ti                      -> HardwareSale 賣 "3060Ti"
      /keyword_add 3060Ti 8000                 -> HardwareSale 賣 "3060Ti" <=8000
      /keyword_add 3060Ti 台北                 -> HardwareSale 賣 "3060Ti 台北"   (not board=3060Ti)
      /keyword_add MacShop macbook             -> MacShop 賣 "macbook"  (known board / alias)
      /keyword_add SomeBoard 賣 3060 Ti 7000   -> SomeBoard 賣 "3060 Ti" <=7000 (explicit category
                                                  marks the first token as a board)
    """
    tokens = [t for t in args if t]
    board, category, max_price = "HardwareSale", "賣", None

    if len(tokens) >= 2:
        try:
            parsed = parse_budget(tokens[-1])
        except ValueError:
            parsed = None
        if parsed:
            max_price = parsed
            tokens = tokens[:-1]

    if len(tokens) >= 3 and tokens[1].lower() in _TG_CATEGORY_WORDS:
        board = resolve_board_alias(tokens[0])
        category = "ALL" if tokens[1].lower() == "all" else tokens[1]
        tokens = tokens[2:]
    elif len(tokens) >= 2 and tokens[0].lower() in COMMON_BOARDS_MAP:
        board = resolve_board_alias(tokens[0])
        tokens = tokens[1:]
        if len(tokens) >= 2 and tokens[0].lower() in _TG_CATEGORY_WORDS:
            category = "ALL" if tokens[0].lower() == "all" else tokens[0]
            tokens = tokens[1:]

    keyword = " ".join(tokens).strip()
    if not keyword:
        return board, category, "", max_price, "請提供欲追蹤之關鍵字"
    return board, category, keyword, max_price, ""


def build_article_keyboard(article_url: str) -> Dict[str, Any]:
    """建立帶有 PTT 原文按鈕的行內鍵盤"""
    return {
        "inline_keyboard": [
            [
                {"text": "前往 PTT 查看原文", "url": article_url}
            ]
        ]
    }


class TelegramNotificationProvider(BaseNotificationProvider):
    """
    Telegram 專屬通知供應商：
    實作 BaseNotificationProvider 之 deliver 與 update_sold 抽象方法。
    """

    platform_name: str = "telegram"

    def __init__(
        self,
        api_client: Optional[TelegramAPIClient] = None,
        token: Optional[str] = None,
        storage: Optional[BotStorage] = None,
    ):
        if api_client is not None:
            self.api = api_client
        elif token:
            self.api = TelegramAPIClient(token=token)
        else:
            self.api = None
        self.storage = storage

    async def close(self) -> None:
        """關閉底層 TelegramAPIClient 之網路會話"""
        if self.api:
            await self.api.close()

    async def deliver(
        self,
        subscriber: Dict[str, Any],
        items: List[Dict[str, Any]],
    ) -> int:
        if not items:
            return 0

        if not self.api:
            logger.warning("[警告] Telegram 推播未配置有效 TelegramAPIClient，略過發送。")
            return 0

        sub_id = subscriber.get("id")
        # 若用戶已標記阻擋或未啟用則跳過
        if subscriber.get("dm_failed") or not subscriber.get("enabled", 1):
            return 0

        target_id_raw = (
            subscriber.get("target_id")
            or subscriber.get("push_channel_id")
            or subscriber.get("discord_id")
        )
        if not target_id_raw:
            logger.warning(f"訂閱者 #{sub_id} 缺乏有效的 Telegram target_id")
            return 0

        # 清除前綴 telegram_ 若存在
        chat_id = str(target_id_raw).replace("telegram_", "").strip()
        scope = subscriber.get("scope", "user")
        scope_label = (
            f"[個人追蹤] {subscriber.get('display_name') or chat_id}"
            if scope == "user"
            else f"[群組追蹤] {subscriber.get('display_name') or '共用'}"
        )

        pushed = 0
        primary_slice = items[:MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER]
        overflow_slice = items[MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER:]

        for it in primary_slice:
            art = it["article"]
            matched_kws = it.get("matched_keywords") or []
            detail = it.get("detail") or {}

            card_html = format_article_card(
                art,
                matched_keywords=matched_kws,
                detail=detail,
                is_sold=False,
                scope_label=scope_label,
            )
            keyboard = build_article_keyboard(art["url"])

            resp = await self.api.send_message(
                chat_id=chat_id,
                text=card_html,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            if not resp.get("ok") and resp.get("error_code") == 400:
                # F17: formatting rejected (e.g. entity parse error) -> plain-text fallback
                logger.warning(
                    f"[警告] Telegram HTML 卡片被拒 (#{sub_id}): {resp.get('description')}，改送純文字版本"
                )
                plain = _plain_text_card(art, detail)
                resp = await self.api.send_message(
                    chat_id=chat_id,
                    text=plain,
                    parse_mode="",
                    reply_markup=keyboard,
                )

            if resp.get("ok"):
                result = resp.get("result", {})
                msg_id = result.get("message_id")
                if self.storage and sub_id is not None:
                    kw_to_record = (
                        it.get("matched_keyword")
                        or (matched_kws[0] if matched_kws else "")
                    )
                    self.storage.record_delivery(
                        subscriber_id=sub_id,
                        article=art,
                        channel_id=str(chat_id),
                        message_id=str(msg_id) if msg_id else None,
                        matched_keyword=kw_to_record,
                    )
                    self.storage.increment_pushed_count(1)
                pushed += 1
                await asyncio.sleep(0.5)  # 遵守每秒發送頻率限制
            elif resp.get("error_code") == 403:
                logger.warning(
                    f"[警告] Telegram 訂閱者 #{sub_id} (Chat: {chat_id}) 已封鎖 Bot，停止推播並標記 dm_failed"
                )
                if self.storage and sub_id is not None:
                    self.storage.mark_subscriber_dm_failed(sub_id, True)
                    self.storage.set_subscriber_enabled(sub_id, False)
                break
            elif resp.get("error_code") == 400:
                # F17: a single malformed card must not block the remaining articles
                logger.error(
                    f"[錯誤] 推播文章給 Telegram #{sub_id} 失敗 (略過此篇): {resp.get('description')}"
                )
                continue
            else:
                logger.error(
                    f"[錯誤] 推播文章給 Telegram #{sub_id} 失敗: {resp.get('description')}"
                )
                break

        # 處理溢出防洗版摘要卡片
        if overflow_slice and pushed > 0:
            for chunk_start in range(0, len(overflow_slice), 10):
                chunk = overflow_slice[chunk_start : chunk_start + 10]
                lines = [f"<b>同時命中的其他新文章 ({len(overflow_slice)} 篇)</b>\n"]
                for idx, it in enumerate(chunk, start=chunk_start + 1):
                    art = it["article"]
                    det = it.get("detail") or {}
                    pr = escape_tg(det.get("extracted_price") or "見內文")
                    t_esc = escape_tg(art["title"][:50])
                    u_esc = escape_tg(art["url"])
                    lines.append(f'{idx}. <a href="{u_esc}">{t_esc}</a> — <code>{pr}</code>')

                summary_text = "\n".join(lines)
                try:
                    ov_resp = await self.api.send_message(
                        chat_id=chat_id, text=summary_text, parse_mode="HTML"
                    )
                    # N3: send_message reports failure via {"ok": False}, not an exception.
                    # Recording a failed summary as delivered would let dedupe block it forever.
                    if not ov_resp.get("ok"):
                        logger.warning(
                            f"[警告] Telegram 溢出摘要卡片發送失敗 (#{sub_id}): {ov_resp.get('description')}"
                        )
                        break
                    if self.storage and sub_id is not None:
                        for it in chunk:
                            self.storage.record_delivery(
                                subscriber_id=sub_id,
                                article=it["article"],
                                channel_id=str(chat_id),
                                message_id=None,
                            )
                except Exception as e:
                    logger.debug(f"發送 Telegram 溢出卡片失敗: {e}")
                    break

        return pushed

    async def update_sold(
        self,
        subscriber: Dict[str, Any],
        delivery_record: Dict[str, Any],
        detail: Dict[str, Any],
    ) -> bool:
        if not self.api:
            return False

        chat_id = delivery_record.get("channel_id")
        msg_id = delivery_record.get("message_id")
        if not chat_id or not msg_id or not str(msg_id).isdigit():
            return False

        art_stub = {
            "url": delivery_record.get("article_url", ""),
            "board": delivery_record.get("board", "HardwareSale"),
            "title": delivery_record.get("title", "已售出商品"),
            "author": delivery_record.get("author", "未知"),
            "category": "賣",
        }
        mkw = delivery_record.get("matched_keyword")
        sold_html = format_article_card(
            art_stub,
            matched_keywords=[mkw] if mkw else [],
            detail=detail,
            # F15: AVAILABLE means a RESERVED / NO_MAIL item is back on sale -> normal card
            is_sold=str(detail.get("sold_state") or "").upper() != "AVAILABLE",
        )
        keyboard = build_article_keyboard(art_stub["url"])

        resp = await self.api.edit_message_text(
            chat_id=chat_id,
            message_id=int(msg_id),
            text=sold_html,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        if resp.get("ok"):
            logger.info(f"[成功] 已成功在 Telegram 原位更新已售出卡片 (Msg #{msg_id})")
            return True
        else:
            desc_lower = str(resp.get("description") or "").lower()
            if "message is not modified" in desc_lower:
                return True
            err_code = resp.get("error_code")
            if err_code in (400, 403):
                logger.warning(
                    f"Telegram 更新已售出訊息失敗 ({err_code}, Msg #{msg_id})，標記 edit_failed=1: {resp.get('description')}"
                )
                d_id = delivery_record.get("id")
                if d_id is not None and self.storage and hasattr(self.storage, "mark_delivery_edit_failed"):
                    self.storage.mark_delivery_edit_failed(int(d_id))
            else:
                logger.debug(
                    f"Telegram 更新已售出訊息略過 (Msg #{msg_id}): {resp.get('description')}"
                )
            return False


class TelegramBot:
    """
    Telegram 機器人客戶端協調器：
    管理與註冊 TelegramNotificationProvider、執行 getUpdates 長輪詢迴圈、
    解析雙向文字指令與處理行內鍵盤 CallbackQuery。
    """

    def __init__(
        self,
        token: str,
        storage: Optional[BotStorage] = None,
        api_client: Optional[TelegramAPIClient] = None,
        crawler: Optional[Any] = None,
        delivery: Optional[Any] = None,
        admin_user_ids: Optional[List[str]] = None,
        blocked_user_ids: Optional[List[str]] = None,
    ):
        self.token = token.strip()
        if blocked_user_ids is None:
            import config as _config_b

            raw_blocked = getattr(_config_b, "TELEGRAM_BLOCKED_USER_IDS", "") or ""
            blocked_user_ids = [
                x.strip() for x in str(raw_blocked).replace(";", ",").split(",") if x.strip()
            ]
        self.blocked_user_ids = {str(x) for x in blocked_user_ids}
        if admin_user_ids is None:
            import config as _config

            raw_admins = getattr(_config, "TELEGRAM_ADMIN_USER_ID", "") or ""
            admin_user_ids = [x.strip() for x in raw_admins.replace(";", ",").split(",") if x.strip()]
        # F05 / N4: TELEGRAM_ADMIN_USER_ID (comma separated) may manage any group's shared rules
        self.admin_user_ids = {str(x) for x in admin_user_ids}
        self._admin_cache: Dict[Tuple[str, str], Tuple[float, bool]] = {}
        self.storage = storage
        self.api = api_client or TelegramAPIClient(token=self.token)
        self.crawler = crawler
        self.delivery = delivery
        self.provider = TelegramNotificationProvider(
            api_client=self.api, storage=self.storage
        )
        if self.delivery and hasattr(self.delivery, "register_provider"):
            self.delivery.register_provider("telegram", self.provider)

        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self.last_update_id: Optional[int] = None
        self.bot_info: Dict[str, Any] = {}
        self.init_error_code: Optional[int] = None

    async def initialize(self) -> bool:
        """驗證 Token 並取得 Bot 基本資料"""
        me_resp = await self.api.get_me()
        if not me_resp.get("ok"):
            try:
                self.init_error_code = int(me_resp.get("error_code") or 500)
            except (TypeError, ValueError):
                self.init_error_code = 500
            logger.error(f"[錯誤] Telegram Bot 初始化失敗: {me_resp.get('description')}")
            return False
        self.init_error_code = None
        self.bot_info = me_resp.get("result", {})
        username = self.bot_info.get("username", "UnknownBot")
        logger.info(f"[成功] Telegram Bot 已就緒: @{username} (ID: {self.bot_info.get('id')})")

        # 向 Telegram 註冊自動補完指令選單
        commands = [
            {"command": "start", "description": "註冊並開啟推播服務"},
            {"command": "keyword_add", "description": "新增關鍵字追蹤"},
            {"command": "keyword_list", "description": "查看目前追蹤關鍵字"},
            {"command": "keyword_remove", "description": "依編號刪除關鍵字"},
            {"command": "pause", "description": "暫停推播通知"},
            {"command": "resume", "description": "恢復推播通知"},
            {"command": "status", "description": "查看系統狀態與監控看板"},
            {"command": "me", "description": "查看個人訂閱資訊與配額"},
            {"command": "forget_me", "description": "永久刪除此對話的所有資料"},
            {"command": "help", "description": "顯示完整使用說明手冊"},
        ]
        await self.api.set_my_commands(commands)
        return True

    def _resolve_subscriber(
        self, chat: Dict[str, Any], user: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """自動依對話取得或建立訂閱者資料記錄"""
        chat_id = str(chat["id"])
        chat_type = chat.get("type", "private")
        scope = "user" if chat_type == "private" else "guild"
        display_name = (
            chat.get("title") or (user.get("first_name") if user else "") or f"TG_{chat_id}"
        )
        if self.storage:
            sub = self.storage.get_or_create_subscriber(
                scope=scope,
                discord_id=f"telegram_{chat_id}",
                display_name=display_name,
                platform="telegram",
                target_id=chat_id,
            )
            return sub
        return {
            "id": 1,
            "scope": scope,
            "discord_id": f"telegram_{chat_id}",
            "display_name": display_name,
            "platform": "telegram",
            "target_id": chat_id,
            "enabled": 1,
        }

    async def _can_write(self, chat: Dict[str, Any], user: Optional[Dict[str, Any]]) -> bool:
        """
        F05: Write operations (add/delete rules, pause/resume) on a GROUP's shared subscriber are
        limited to TELEGRAM_ADMIN_USER_ID or the group's creator/administrators. Private chats
        always manage their own personal scope.
        """
        if chat.get("type", "private") == "private":
            return True
        uid = str((user or {}).get("id") or "")
        if not uid:
            return False
        if uid in self.admin_user_ids:
            return True
        key = (str(chat.get("id")), uid)
        now = time.time()
        cached = self._admin_cache.get(key)
        if cached and now - cached[0] < 120:
            return cached[1]
        allowed = False
        try:
            resp = await self.api.get_chat_member(chat["id"], uid)
            status = ((resp or {}).get("result") or {}).get("status", "")
            allowed = status in ("creator", "administrator")
        except Exception as e:
            logger.warning(f"[警告] 查詢 Telegram 群組成員權限失敗: {e}")
            return False
        self._admin_cache[key] = (now, allowed)
        return allowed

    def _is_blocked(self, user: Optional[Dict[str, Any]]) -> bool:
        """R06: blocked users (TELEGRAM_BLOCKED_USER_IDS or the database blacklist) get no response."""
        uid = str((user or {}).get("id") or "")
        if not uid:
            return False
        if uid in self.blocked_user_ids:
            return True
        if self.storage is not None:
            try:
                return self.storage.is_blacklisted(f"telegram_{uid}") is True
            except Exception:
                return False
        return False

    async def _deny_write(self, chat_id: Union[int, str]) -> None:
        await self.api.send_message(
            chat_id,
            "[權限不足] 群組共用追蹤設定僅限群組管理員或機器人管理者修改。個人追蹤請私訊機器人。",
        )

    async def _handle_message(self, message: Dict[str, Any]) -> None:
        text = (message.get("text") or "").strip()
        if not text or not text.startswith("/"):
            return

        chat = message.get("chat")
        if not chat or not isinstance(chat, dict) or not chat.get("id"):
            return
        user = message.get("from")
        if self._is_blocked(user):
            return
        sub = self._resolve_subscriber(chat, user)
        chat_id = chat["id"]

        parts = text.split()
        raw_cmd = parts[0].lower()
        # 移除群組中的 @bot_username 後綴 (如 /start@MyBot -> /start)
        cmd = raw_cmd.split("@")[0]
        args = parts[1:]

        if cmd in (
            "/keyword_add",
            "/add",
            "/pause",
            "/resume",
            "/keyword_remove",
            "/remove",
            "/forget_me",
        ) and not await self._can_write(
            chat, user
        ):
            await self._deny_write(chat_id)
            return

        if cmd == "/start":
            await self._cmd_start(chat_id, sub)
        elif cmd == "/help":
            await self._cmd_help(chat_id)
        elif cmd in ("/keyword_add", "/add"):
            await self._cmd_keyword_add(chat_id, sub, args)
        elif cmd in ("/keyword_list", "/list"):
            await self._cmd_keyword_list(chat_id, sub)
        elif cmd in ("/keyword_remove", "/remove"):
            await self._cmd_keyword_remove(chat_id, sub, args)
        elif cmd == "/forget_me":
            await self._cmd_forget_me(chat_id, sub)
        elif cmd == "/pause":
            if self.storage:
                self.storage.set_subscriber_enabled(sub["id"], False)
            await self.api.send_message(
                chat_id, "[資訊] 已暫停推播通知。隨時可輸入 /resume 恢復推播。"
            )
        elif cmd == "/resume":
            if self.storage:
                self.storage.set_subscriber_enabled(sub["id"], True)
            await self.api.send_message(chat_id, "[成功] 已恢復推播通知！")
        elif cmd == "/status":
            await self._cmd_status(chat_id)
        elif cmd == "/me":
            await self._cmd_me(chat_id, sub)

    async def _cmd_start(self, chat_id: Union[int, str], sub: Dict[str, Any]) -> None:
        welcome_text = (
            "<b>[資訊] 歡迎使用 PTT 智慧追蹤機器人 (Telegram 版)</b>\n\n"
            f"您的專屬 Chat ID: <code>{chat_id}</code>\n"
            f"訂閱者代號: <code>#{sub.get('id', 1)}</code>\n"
            f"目前推播狀態: <b>{'[啟用中]' if sub.get('enabled') else '[已暫停]'}</b>\n\n"
            "<b>常用指令列表：</b>\n"
            "• <code>/keyword_add &lt;看板&gt; &lt;分類&gt; &lt;關鍵字&gt; [預算]</code> - 新增關鍵字\n"
            "• <code>/keyword_list</code> - 查看並管理已設定關鍵字\n"
            "• <code>/keyword_remove &lt;編號&gt;</code> - 依清單編號刪除關鍵字\n"
            "• <code>/pause</code> 與 <code>/resume</code> - 切換推播開關\n"
            "• <code>/status</code> - 檢視看板監控即時狀態\n"
            "• <code>/me</code> - 檢視個人配額與訂閱資料\n"
            "• <code>/forget_me</code> - 永久刪除此對話的所有資料\n"
            "• <code>/help</code> - 取得詳細說明"
        )
        await self.api.send_message(chat_id, welcome_text, parse_mode="HTML")

    async def _cmd_help(self, chat_id: Union[int, str]) -> None:
        help_text = (
            "<b>[操作手冊] PTT 智慧追蹤機器人指令說明</b>\n\n"
            "<b>1. 新增關鍵字追蹤：</b>\n"
            "• 完整語法：<code>/keyword_add &lt;看板&gt; &lt;分類&gt; &lt;關鍵字&gt; [最高預算]</code>\n"
            "  範例：<code>/keyword_add HardwareSale 賣 3060Ti 7000</code>\n"
            "• 簡化語法 (預設 HardwareSale 賣)：\n"
            "  範例：<code>/keyword_add 4070Super 18000</code>\n\n"
            "<b>2. 管理追蹤清單：</b>\n"
            "• <code>/keyword_list</code>：列出目前所有規則，點擊按鈕即可一鍵刪除。\n"
            "• <code>/keyword_remove &lt;編號&gt;</code>：依 /keyword_list 顯示的 #編號 刪除。\n\n"
            "<b>3. 系統與推播控制：</b>\n"
            "• <code>/pause</code>：暫停通知\n"
            "• <code>/resume</code>：重啟通知\n"
            "• <code>/status</code>：查看各看板連線與更新狀態\n"
            "• <code>/me</code>：查看今日配額與規則上限\n\n"
            "<b>4. 資料刪除：</b>\n"
            "• <code>/forget_me</code>：永久刪除此對話的關鍵字、推播紀錄與訂閱資料（群組限管理員）"
        )
        await self.api.send_message(chat_id, help_text, parse_mode="HTML")

    async def _cmd_keyword_add(
        self, chat_id: Union[int, str], sub: Dict[str, Any], args: List[str]
    ) -> None:
        if not args:
            await self.api.send_message(
                chat_id,
                "[錯誤] 請提供欲追蹤之關鍵字！\n範例：<code>/keyword_add HardwareSale 賣 3060Ti 7000</code> 或 <code>/keyword_add 3060Ti</code>",
                parse_mode="HTML",
            )
            return

        if not self.storage:
            await self.api.send_message(chat_id, "[錯誤] 資料庫尚未連線", parse_mode="HTML")
            return

        board, category, kw, max_price, perr = parse_keyword_add_args(args)
        if perr:
            await self.api.send_message(chat_id, f"[錯誤] {escape_tg(perr)}", parse_mode="HTML")
            return

        # N2: resolve aliases and verify boards not yet tracked (same as the Discord flow), so a
        # typo can never register a non-existent board into the global crawl loop.
        if board.upper() != "ALL" and not self.storage.is_board_tracked(board):
            if self.crawler is None or not hasattr(self.crawler, "verify_board"):
                await self.api.send_message(
                    chat_id, f"[錯誤] 看板 {escape_tg(board)} 尚未追蹤且無法驗證", parse_mode="HTML"
                )
                return
            valid, canonical, v_msg = await self.crawler.verify_board(board)
            if not valid:
                await self.api.send_message(
                    chat_id, f"[錯誤] 看板驗證失敗：{escape_tg(v_msg)}", parse_mode="HTML"
                )
                return
            board = canonical

        ok, msg = self.storage.add_keyword(
            keyword=kw,
            board=board,
            category=category,
            subscriber_id=sub["id"],
            max_price=max_price,
        )

        prefix = "[成功]" if ok else "[錯誤]"
        await self.api.send_message(chat_id, f"{prefix} {escape_tg(msg)}", parse_mode="HTML")

    async def _cmd_keyword_remove(
        self, chat_id: Union[int, str], sub: Dict[str, Any], args: List[str]
    ) -> None:
        if not self.storage:
            await self.api.send_message(chat_id, "[錯誤] 資料庫尚未連線", parse_mode="HTML")
            return
        ident = (args[0] if args else "").lstrip("#").strip()
        if not ident:
            await self.api.send_message(
                chat_id,
                "[錯誤] 請提供要刪除的關鍵字編號，例如 <code>/keyword_remove 12</code>"
                "（編號可用 /keyword_list 查看）",
                parse_mode="HTML",
            )
            return
        removed = self.storage.remove_keyword(ident, subscriber_id=sub["id"])
        if removed:
            await self.api.send_message(
                chat_id,
                f"[成功] 已刪除關鍵字「{escape_tg(removed['keyword'])}」",
                parse_mode="HTML",
            )
        else:
            await self.api.send_message(
                chat_id, "[提示] 找不到這個編號的關鍵字，請用 /keyword_list 確認編號。", parse_mode="HTML"
            )

    async def _cmd_forget_me(self, chat_id: Union[int, str], sub: Dict[str, Any]) -> None:
        """R06: ask for confirmation; the actual deletion happens in the callback handler."""
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "確認永久刪除", "callback_data": f"forget_yes:{sub['id']}"},
                    {"text": "取消", "callback_data": "forget_no"},
                ]
            ]
        }
        await self.api.send_message(
            chat_id,
            "<b>[確認] 即將永久刪除此對話的所有資料</b>\n"
            "包含追蹤關鍵字、推播紀錄與訂閱設定，刪除後無法復原。\n"
            "確定要刪除嗎？",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    async def _cmd_keyword_list(
        self, chat_id: Union[int, str], sub: Dict[str, Any]
    ) -> None:
        if not self.storage:
            await self.api.send_message(chat_id, "[錯誤] 資料庫尚未連線", parse_mode="HTML")
            return

        kws = self.storage.get_keywords(subscriber_id=sub["id"])
        if not kws:
            await self.api.send_message(
                chat_id,
                "[資訊] 您目前尚未設定任何追蹤關鍵字。輸入 <code>/keyword_add 關鍵字</code> 即可開始追蹤！",
                parse_mode="HTML",
            )
            return

        lines = [f"<b>[資訊] 目前追蹤關鍵字清單 (共 {len(kws)} 組)</b>\n"]
        buttons = []

        for idx, k in enumerate(kws, start=1):
            p_str = f" (≤${k['max_price']})" if k.get("max_price") else " (不限預算)"
            lines.append(
                f"{idx}. <b>[{escape_tg(k['board'])}] [{escape_tg(k['category'])}] {escape_tg(k['keyword'])}</b>{p_str}"
            )
            buttons.append(
                [
                    {
                        "text": f"刪除 #{k['id']} {k['keyword'][:12]}",
                        "callback_data": f"del_kw:{k['id']}",
                    }
                ]
            )

        keyboard = {"inline_keyboard": buttons}
        await self.api.send_message(
            chat_id, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard
        )

    async def _cmd_status(self, chat_id: Union[int, str]) -> None:
        if not self.storage:
            await self.api.send_message(chat_id, "[錯誤] 資料庫尚未連線", parse_mode="HTML")
            return

        boards = self.storage.get_boards()
        total_pushed = self.storage.get_setting("total_pushed", "0")

        lines = [
            "<b>[系統狀態] PTT 智慧追蹤機器人</b>\n",
            f"• 監控看板總數: {len(boards)} 個",
            f"• 歷史推播總計: {total_pushed} 篇\n",
            "<b>看板監控即時狀態：</b>",
        ]
        for b in boards[:10]:
            init_str = "[已同步]" if b.get("initialized") else "[同步中]"
            lines.append(
                f"• <b>{escape_tg(b['name'])}</b>: {init_str} {escape_tg(b.get('last_status', ''))}"
            )

        await self.api.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    async def _cmd_me(self, chat_id: Union[int, str], sub: Dict[str, Any]) -> None:
        if not self.storage:
            await self.api.send_message(chat_id, "[錯誤] 資料庫尚未連線", parse_mode="HTML")
            return

        quota = self.storage.get_subscriber_quota(sub["id"])
        kws = self.storage.get_keywords(subscriber_id=sub["id"])
        daily_scans = self.storage.get_daily_usage(sub["id"], "scan")

        lines = [
            f"<b>[個人資訊] 訂閱者 #{sub.get('id', 1)}</b>\n",
            f"• 平台: <code>{sub.get('platform', 'telegram')}</code>",
            f"• 權限等級: <code>{sub.get('tier', 'free')}</code>",
            f"• 推播開關: <b>{'[開啟]' if sub.get('enabled') else '[關閉]'}</b>",
            f"• 關鍵字用量: <code>{len(kws)} / {quota.get('max_keywords', 10)}</code> 組",
            f"• 今日掃描次數: <code>{daily_scans} / {quota.get('daily_scans', 8)}</code> 次",
        ]
        await self.api.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    async def _handle_callback_query(self, cb: Dict[str, Any]) -> None:
        cb_id = cb["id"]
        data = cb.get("data", "")
        msg = cb.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        user = cb.get("from", {})

        if self._is_blocked(user):
            return

        if not chat_id:
            await self.api.answer_callback_query(cb_id, text="[錯誤] 無效操作")
            return

        if data == "forget_no":
            await self.api.answer_callback_query(cb_id, text="[資訊] 已取消，資料未變更")
            return

        sub = self._resolve_subscriber(chat, user)

        if data.startswith("forget_yes:"):
            if not await self._can_write(chat, user):
                await self.api.answer_callback_query(
                    cb_id, text="[權限不足] 僅限群組管理員刪除群組資料", show_alert=True
                )
                return
            # The button must belong to THIS chat's subscriber (never trust the id in the payload)
            if data.split(":", 1)[1] != str(sub["id"]) or not self.storage:
                await self.api.answer_callback_query(cb_id, text="[錯誤] 此確認按鈕已失效")
                return
            self.storage.forget_subscriber_by_id(sub["id"])
            await self.api.answer_callback_query(cb_id, text="[成功] 資料已永久刪除")
            await self.api.send_message(
                chat_id,
                "[成功] 此對話的關鍵字、推播紀錄與訂閱資料已永久刪除。之後再次使用指令會建立全新的訂閱。",
                parse_mode="HTML",
            )
            return

        if data.startswith("del_kw:"):
            if not await self._can_write(chat, user):
                await self.api.answer_callback_query(
                    cb_id, text="[權限不足] 僅限群組管理員刪除共用規則", show_alert=True
                )
                return
            if not self.storage:
                await self.api.answer_callback_query(cb_id, text="[錯誤] 資料庫未連線")
                return
            kw_id_str = data.split(":")[1]
            removed = self.storage.remove_keyword(kw_id_str, subscriber_id=sub["id"])
            if removed:
                await self.api.answer_callback_query(
                    cb_id, text=f"[成功] 已刪除關鍵字「{removed['keyword']}」"
                )
                # 重新整理關鍵字清單訊息
                await self._cmd_keyword_list(chat_id, sub)
            else:
                await self.api.answer_callback_query(
                    cb_id, text="[提示] 關鍵字已不存在或已被刪除"
                )
        else:
            await self.api.answer_callback_query(cb_id, text="[提示] 未知指令")

    async def _process_update(self, update: Dict[str, Any]) -> None:
        if "message" in update:
            await self._handle_message(update["message"])
        elif "callback_query" in update:
            await self._handle_callback_query(update["callback_query"])

    async def start_polling(self) -> None:
        """啟動 Long Polling 背景迴圈"""
        self._running = True
        logger.info("[資訊] Telegram Bot 長輪詢 (Long Polling) 服務啟動")

        fail_backoff = 5.0
        while self._running:
            try:
                updates = await self.api.get_updates(offset=self.last_update_id, timeout=20)
                # R05: a rejected getUpdates returns an empty list immediately; without a pause
                # this loop would re-send the request continuously.
                err_code = getattr(self.api, "last_error_code", None)
                if not updates and isinstance(err_code, int):
                    err_desc = getattr(self.api, "last_error_description", "")
                    if err_code in (401, 404):
                        logger.error(
                            f"[錯誤] Telegram Bot Token 無效 (HTTP {err_code})，已停止 Telegram 長輪詢: {err_desc}"
                        )
                        self._running = False
                        break
                    if err_code == 409:
                        wait_s = 30.0
                        logger.error(
                            "[錯誤] Telegram 回報 409 Conflict：同一個 Bot Token 正被另一個程式輪詢"
                            "（例如兩台機器同時執行），30 秒後重試"
                        )
                    else:
                        wait_s = fail_backoff
                        fail_backoff = min(60.0, fail_backoff * 2)
                        logger.warning(
                            f"[警告] Telegram getUpdates 失敗 (HTTP {err_code})，{wait_s:.0f} 秒後重試: {err_desc}"
                        )
                    await asyncio.sleep(wait_s)
                    continue
                fail_backoff = 5.0
                for u in updates:
                    u_id = u.get("update_id") if isinstance(u, dict) else None
                    if u_id is None:
                        continue
                    self.last_update_id = u_id + 1
                    try:
                        await self._process_update(u)
                    except Exception as e:
                        logger.error(
                            f"[錯誤] 處理 Telegram Update #{u_id} 發生例外: {e}",
                            exc_info=True,
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[警告] Telegram 輪詢異常，5 秒後重試: {e}")
                await asyncio.sleep(5.0)

    async def stop(self) -> None:
        """優雅關閉機器人與非同步連線"""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self.api.close()
        logger.info("[資訊] Telegram Bot 已完全停止")
