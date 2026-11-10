"""
PTT 智慧追蹤機器人 - Email (SMTP) 郵件推播通知模組 (email_notifier.py)

本模組遵循零外部依賴原則，全數使用 Python 標準函式庫實作：
- smtplib / ssl: 支援 STARTTLS (Port 587) 與 SSL (Port 465) 安全連線認證
- email.message.EmailMessage / email.utils: 現代化 MIME multipart/alternative 結構
- asyncio.to_thread: 將同步阻塞式 SMTP 發信調度至背景執行緒集區，維持非同步主迴圈流暢
- html.escape: 嚴密過濾 PTT 文章內容，防止 HTML / XSS 注入攻擊
- re / parseaddr: 支援單一或逗號分隔多收件人解析與正則防禦
"""

import asyncio
import html
import logging
import os
import re
import smtplib
import socket
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Any, Dict, List, Optional, Tuple, Union

from config import MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER
from delivery import BaseNotificationProvider
from storage import BotStorage

logger = logging.getLogger("email_notifier")

# 正則驗證 RFC 5322 電子郵件格式
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def clean_header_value(val: Any) -> str:
    """
    清理郵件標頭值，消除換行符號 (\\r, \\n) 與製表符，
    防禦 SMTP 標頭注入攻擊 (Header Injection)，並避免 Python 3.13+ EmailPolicy 引發 ValueError。
    """
    if val is None:
        return ""
    return re.sub(r"[\r\n\t]+", " ", str(val)).strip()


def parse_recipient_list(raw_recipients: Union[str, List[str], None]) -> List[str]:
    """
    解析收件者字串或清單（支援逗號、分號、換行分隔），驗證格式並去除重複項。
    """
    if not raw_recipients:
        return []

    if isinstance(raw_recipients, str):
        candidates = re.split(r"[,;\s\n]+", raw_recipients)
    elif isinstance(raw_recipients, (list, tuple, set)):
        candidates = []
        for item in raw_recipients:
            if isinstance(item, str):
                candidates.extend(re.split(r"[,;\s\n]+", item))
    else:
        return []

    valid_emails: List[str] = []
    seen = set()
    for raw in candidates:
        token = raw.strip()
        if not token:
            continue
        # 處理 "Name <user@domain.com>" 格式
        _, addr = parseaddr(token)
        clean_addr = (addr or token).strip()
        if EMAIL_REGEX.match(clean_addr):
            lower_addr = clean_addr.lower()
            if lower_addr not in seen:
                seen.add(lower_addr)
                valid_emails.append(clean_addr)
    return valid_emails


class EmailNotificationProvider(BaseNotificationProvider):
    """
    Email (SMTP) 郵件推播通知供應商：
    實作 BaseNotificationProvider 介面，支援非同步發送響應式 HTML 郵件卡片。
    """

    platform_name: str = "email"

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        use_tls: Optional[bool] = None,
        from_addr: Optional[str] = None,
        from_name: Optional[str] = None,
        default_to: Optional[Union[str, List[str]]] = None,
        timeout: float = 15.0,
        storage: Optional[BotStorage] = None,
    ):
        self.host = (host or os.getenv("SMTP_HOST", "smtp.gmail.com")).strip()
        raw_port = port if port is not None else os.getenv("SMTP_PORT", "587")
        try:
            self.port = int(raw_port)
        except (ValueError, TypeError):
            self.port = 587

        self.user = (user or os.getenv("SMTP_USER", "")).strip()
        self.password = (password or os.getenv("SMTP_PASSWORD", "")).strip()

        if use_tls is not None:
            self.use_tls = bool(use_tls)
        else:
            raw_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower()
            self.use_tls = raw_tls in ("true", "1", "yes", "t")

        self.from_addr = (from_addr or os.getenv("EMAIL_FROM", "") or self.user).strip()
        self.from_name = (from_name or os.getenv("EMAIL_FROM_NAME", "") or "PTT 智慧追蹤機器人").strip()

        raw_to = default_to if default_to is not None else (
            os.getenv("EMAIL_TO", "") or os.getenv("EMAIL_TARGET_RECIPIENT", "")
        )
        self.default_to_emails = parse_recipient_list(raw_to)
        self.timeout = float(timeout)
        self.storage = storage

    def is_configured(self) -> bool:
        """檢查 SMTP 基本連線與認證參數是否已完備。"""
        return bool(self.host and self.user and self.password)

    def _sync_send(self, to_addrs: List[str], msg: EmailMessage) -> bool:
        """
        同步 SMTP 發送實作，交由 asyncio.to_thread 在獨立執行緒內運作。
        嚴密捕捉各類連線、認證與超時例外，保證不造成系統崩潰。
        """
        if not to_addrs:
            logger.warning("[警告] Email 發送失敗：無有效收件者地址")
            return False

        from_addr = self.from_addr or self.user

        try:
            context = ssl.create_default_context()
            if self.port == 465:
                # SSL 加密通道 (Port 465)
                with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=self.timeout) as server:
                    if self.user and self.password:
                        server.login(self.user, self.password)
                    server.send_message(msg, from_addr=from_addr, to_addrs=to_addrs)
            else:
                # STARTTLS 加密通道 (預設 Port 587 或其他自訂埠)
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
                    server.ehlo()
                    if self.use_tls:
                        server.starttls(context=context)
                        server.ehlo()
                    if self.user and self.password:
                        server.login(self.user, self.password)
                    server.send_message(msg, from_addr=from_addr, to_addrs=to_addrs)
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(
                f"[錯誤] SMTP 認證失敗：帳號或應用程式密碼錯誤 ({self.user}@{self.host})。若為 Gmail 請確認啟用兩步驟驗證並建立 16 碼專用密碼: {e}"
            )
            return False
        except smtplib.SMTPConnectError as e:
            logger.error(f"[錯誤] 無法連線至 SMTP 伺服器 ({self.host}:{self.port}): {e}")
            return False
        except smtplib.SMTPServerDisconnected as e:
            logger.error(f"[錯誤] SMTP 伺服器連線意外中斷 ({self.host}:{self.port}): {e}")
            return False
        except (socket.timeout, TimeoutError) as e:
            logger.error(f"[錯誤] 連線或傳輸至 SMTP 伺服器超時 ({self.timeout}秒): {e}")
            return False
        except smtplib.SMTPRecipientsRefused as e:
            logger.error(f"[錯誤] 收件者地址遭 SMTP 伺服器退信拒收 ({to_addrs}): {e}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"[錯誤] SMTP 郵件傳輸異常: {e}")
            return False
        except Exception as e:
            logger.error(f"[錯誤] 發送電子郵件時發生未預期例外: {e}", exc_info=True)
            return False

    def _build_single_article_message(
        self,
        subscriber: Dict[str, Any],
        item: Dict[str, Any],
        to_addrs: List[str],
        scope_label: str = "",
    ) -> EmailMessage:
        """建立單篇 PTT 文章通知之 MIME multipart/alternative 郵件。"""
        art = item.get("article", {})
        matched_kws = item.get("matched_keywords") or []
        detail = item.get("detail") or {}
        fuzzy_matches = item.get("fuzzy_matches") or []

        board = art.get("board", "HardwareSale")
        title = art.get("title", "未命名文章")
        author = art.get("author", "未知")
        url = art.get("url", "")
        category = art.get("category", "")
        date_str = art.get("date", "-")
        nrec = art.get("nrec", "0")

        price = detail.get("extracted_price") or "見內文"
        location = detail.get("extracted_location") or "未標註"
        snippet = detail.get("snippet") or ""
        buyer_queue = detail.get("buyer_queue_count", 0)
        ai_note = detail.get("ai_summary_note") or ""

        # 主旨與防注入清理
        clean_board = clean_header_value(board)
        clean_title = clean_header_value(title)
        clean_price = clean_header_value(price)
        subject_str = clean_header_value(f"[PTT {clean_board}] {clean_title} — {clean_price}")

        msg = EmailMessage()
        msg["Subject"] = subject_str
        msg["From"] = formataddr((self.from_name, self.from_addr or self.user))
        msg["To"] = ", ".join(to_addrs)

        # 1. Plain-Text 純文字備援
        plain_lines = [
            f"【PTT {board} 智慧監控新文章】",
            f"文章標題：{title}",
            f"欲售價格：{price}",
            f"文章作者：{author} (推文數: {nrec}, 發文日: {date_str})",
            f"交易地點：{location}",
        ]
        if matched_kws:
            plain_lines.append(f"命中關鍵字：{', '.join(matched_kws)}")
        if fuzzy_matches:
            f_strs = [f"{m.get('rule_id')}: {m.get('description')} ({m.get('reason')})" for m in fuzzy_matches]
            plain_lines.append(f"AI 語意命中：{'; '.join(f_strs)}")
        if ai_note:
            plain_lines.append(f"AI 摘要評估：{ai_note}")
        if buyer_queue > 0:
            plain_lines.append(f"買方排隊狀況：已有 {buyer_queue} 位買家留言 / 排隊")
        plain_lines.extend([
            f"原始網址：{url}",
            "",
            "--- 內文重點摘要 ---",
            snippet[:1000] if snippet else "（無內文預覽）",
            "",
            f"{scope_label} | PTT 智慧追蹤機器人 (系統自動發送，請勿直接回覆)",
        ])
        msg.set_content("\n".join(plain_lines))

        # 2. Responsive HTML 卡片版型 (嚴密跳脫所有外部欄位)
        safe_board = html.escape(board)
        safe_title = html.escape(title)
        safe_author = html.escape(author)
        safe_category = html.escape(category or "一般")
        safe_date = html.escape(date_str)
        safe_nrec = html.escape(str(nrec))
        safe_price = html.escape(price)
        safe_location = html.escape(location)
        safe_snippet = html.escape(snippet[:1000]) if snippet else ""
        safe_ai_note = html.escape(ai_note)
        safe_scope_label = html.escape(scope_label)
        safe_url = html.escape(url, quote=True)

        # 關鍵字 Badge 渲染
        kw_badges_html = ""
        if matched_kws:
            kw_items = "".join(
                f'<span style="background-color: #fef3c7; color: #92400e; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-right: 6px; font-weight: 600;">{html.escape(k)}</span>'
                for k in matched_kws
            )
            kw_badges_html = f"""
            <tr>
              <td style="padding: 6px 0; color: #64748b; width: 90px; vertical-align: top;">命中關鍵字：</td>
              <td style="padding: 6px 0; color: #0f172a;">{kw_items}</td>
            </tr>
            """

        # AI 語意命中渲染
        fuzzy_html = ""
        if fuzzy_matches:
            fz_items = "".join(
                f'<div style="margin-bottom: 4px;"><span style="color: #6b21a8; font-weight: 600;">• [{html.escape(str(m.get("rule_id", "")))}]</span> {html.escape(m.get("description", ""))}<br><span style="font-size: 11px; color: #64748b;">({html.escape(m.get("reason", ""))})</span></div>'
                for m in fuzzy_matches
            )
            fuzzy_html = f"""
            <tr>
              <td style="padding: 6px 0; color: #64748b; width: 90px; vertical-align: top;">AI語意命中：</td>
              <td style="padding: 6px 0; color: #0f172a;">{fz_items}</td>
            </tr>
            """

        # AI 摘要筆記渲染
        ai_note_html = ""
        if safe_ai_note:
            ai_note_html = f"""
            <tr>
              <td style="padding: 6px 0; color: #64748b; width: 90px; vertical-align: top;">AI摘要評估：</td>
              <td style="padding: 6px 0; color: #0f172a; font-style: italic;">{safe_ai_note}</td>
            </tr>
            """

        # 內文摘要區塊
        snippet_block_html = ""
        if safe_snippet:
            snippet_block_html = f"""
            <div style="margin-top: 16px;">
              <div style="font-size: 12px; color: #64748b; font-weight: 600; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px;">內文重點預覽</div>
              <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px 14px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 13px; color: #334155; line-height: 1.6; white-space: pre-wrap; word-break: break-word;">{safe_snippet}</div>
            </div>
            """

        # 買方排隊狀況
        queue_text = f"已有 {buyer_queue} 位買家留言 / 排隊" if buyer_queue > 0 else "尚無買家排隊"

        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title}</title>
</head>
<body style="margin: 0; padding: 16px; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #cbd5e1; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
    
    <!-- Header Banner -->
    <div style="background-color: #1e293b; padding: 14px 20px; color: #ffffff;">
      <span style="background-color: #0284c7; color: #ffffff; padding: 3px 10px; border-radius: 4px; font-size: 12px; font-weight: bold; letter-spacing: 0.5px;">{safe_board}</span>
      <span style="background-color: #334155; color: #e2e8f0; padding: 3px 8px; border-radius: 4px; font-size: 12px; margin-left: 6px;">{safe_category}</span>
      <span style="font-size: 13px; margin-left: 8px; color: #94a3b8;">PTT 新文章盯梢通知</span>
    </div>

    <!-- Body Content -->
    <div style="padding: 20px;">
      <h2 style="margin: 0 0 14px 0; font-size: 18px; line-height: 1.4;">
        <a href="{safe_url}" style="color: #0284c7; text-decoration: none;">{safe_title}</a>
      </h2>

      <!-- Price Highlight Box -->
      <div style="background-color: #f0fdf4; border-left: 4px solid #22c55e; padding: 12px 16px; margin-bottom: 18px; border-radius: 2px;">
        <span style="color: #166534; font-size: 13px; font-weight: 600;">欲售價格 / 預算：</span>
        <span style="color: #dc2626; font-size: 20px; font-weight: bold; margin-left: 6px;">{safe_price}</span>
      </div>

      <!-- Metadata Table -->
      <table style="width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 12px;">
        <tr>
          <td style="padding: 6px 0; color: #64748b; width: 90px;">作者：</td>
          <td style="padding: 6px 0; color: #0f172a; font-weight: 600;">{safe_author}</td>
        </tr>
        <tr>
          <td style="padding: 6px 0; color: #64748b;">發文日期：</td>
          <td style="padding: 6px 0; color: #0f172a;">{safe_date} (推文數: {safe_nrec})</td>
        </tr>
        <tr>
          <td style="padding: 6px 0; color: #64748b;">交易地點：</td>
          <td style="padding: 6px 0; color: #0f172a;">{safe_location}</td>
        </tr>
        <tr>
          <td style="padding: 6px 0; color: #64748b;">排隊狀況：</td>
          <td style="padding: 6px 0; color: #0f172a;">{queue_text}</td>
        </tr>
        {kw_badges_html}
        {fuzzy_html}
        {ai_note_html}
      </table>

      <!-- Snippet Preview -->
      {snippet_block_html}

      <!-- CTA Button -->
      <div style="text-align: center; margin: 24px 0 12px 0;">
        <a href="{safe_url}" style="display: inline-block; background-color: #0284c7; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: bold; letter-spacing: 0.5px;">前往 PTT 查看原始文章</a>
      </div>
    </div>

    <!-- Footer -->
    <div style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 12px 20px; text-align: center; font-size: 12px; color: #94a3b8;">
      {safe_scope_label} | PTT 智慧追蹤機器人<br>
      <span style="font-size: 11px; color: #cbd5e1;">此為系統自動發送之通知郵件，請勿直接回覆。</span>
    </div>
  </div>
</body>
</html>"""
        msg.add_alternative(html_body, subtype="html")
        return msg

    def _build_overflow_message(
        self,
        subscriber: Dict[str, Any],
        overflow_items: List[Dict[str, Any]],
        to_addrs: List[str],
        scope_label: str,
    ) -> EmailMessage:
        """建立超出推送上限文章之防洗版摘要清單郵件。"""
        msg = EmailMessage()
        subject_str = f"[PTT 防洗版摘要] 同時命中的其他新文章 ({len(overflow_items)} 篇)"
        msg["Subject"] = clean_header_value(subject_str)
        msg["From"] = formataddr((self.from_name, self.from_addr or self.user))
        msg["To"] = ", ".join(to_addrs)

        plain_lines = [
            f"【PTT 防洗版摘要】本次巡檢同時命中其他 {len(overflow_items)} 篇新文章：",
            "",
        ]
        html_rows = []
        for idx, it in enumerate(overflow_items, start=1):
            art = it.get("article", {})
            det = it.get("detail") or {}
            pr = det.get("extracted_price") or "見內文"
            plain_lines.append(f"{idx}. [{art.get('board')}] {art.get('title')} ({pr}) - {art.get('url')}")
            html_rows.append(
                f"""<tr>
                  <td style="padding: 8px; border-bottom: 1px solid #e2e8f0; width: 30px; text-align: center; color: #64748b;">{idx}</td>
                  <td style="padding: 8px; border-bottom: 1px solid #e2e8f0;">
                    <span style="background-color: #e2e8f0; color: #475569; padding: 2px 6px; border-radius: 3px; font-size: 11px;">{html.escape(art.get('board', ''))}</span>
                    <a href="{html.escape(art.get('url', ''), quote=True)}" style="color: #0284c7; text-decoration: none; font-weight: 500; margin-left: 4px;">{html.escape(art.get('title', ''))}</a>
                  </td>
                  <td style="padding: 8px; border-bottom: 1px solid #e2e8f0; width: 100px; text-align: right; color: #dc2626; font-weight: 600;">{html.escape(pr)}</td>
                </tr>"""
            )

        plain_lines.extend(["", f"{scope_label} | PTT 智慧追蹤機器人"])
        msg.set_content("\n".join(plain_lines))

        html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin: 0; padding: 16px; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
  <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #cbd5e1; overflow: hidden;">
    <div style="background-color: #d97706; padding: 12px 20px; color: #ffffff; font-weight: bold; font-size: 15px;">
      PTT 同時命中其他新文章摘要 ({len(overflow_items)} 篇)
    </div>
    <div style="padding: 16px;">
      <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
        {''.join(html_rows)}
      </table>
    </div>
    <div style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 10px 20px; text-align: center; font-size: 11px; color: #94a3b8;">
      {html.escape(scope_label)} | 防洗版合併摘要卡片
    </div>
  </div>
</body>
</html>"""
        msg.add_alternative(html_body, subtype="html")
        return msg

    async def deliver(
        self,
        subscriber: Dict[str, Any],
        items: List[Dict[str, Any]],
    ) -> int:
        """
        將已過濾之命中文章以非同步 SMTP 發送至訂閱者所設定之信箱。
        回傳成功發送數。
        """
        if not items:
            return 0

        if not self.is_configured():
            logger.warning("[警告] Email 推播未配置完整 SMTP 伺服器主機、帳號或密碼，略過發送。")
            return 0

        # 解析目標收件者
        sub_id = subscriber.get("id")
        target_field = subscriber.get("target_id") or subscriber.get("display_name")
        to_addrs = parse_recipient_list(target_field) or self.default_to_emails
        if not to_addrs:
            logger.warning(f"[警告] 訂閱者 #{sub_id} (平台: email) 未指定有效收件信箱且無全域預設信箱，略過推播。")
            return 0

        scope = subscriber.get("scope", "user")
        scope_label = (
            f"[個人追蹤] {subscriber.get('display_name') or to_addrs[0]}"
            if scope == "user"
            else f"[伺服器/群組追蹤] {subscriber.get('display_name') or '共用'}"
        )

        pushed = 0
        primary_slice = items[:MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER]
        overflow_slice = items[MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER:]

        for it in primary_slice:
            art = it.get("article", {})
            msg = self._build_single_article_message(subscriber, it, to_addrs, scope_label)
            ok = await asyncio.to_thread(self._sync_send, to_addrs, msg)
            if ok:
                if self.storage and sub_id is not None:
                    self.storage.record_delivery(
                        subscriber_id=sub_id,
                        article=art,
                        channel_id="email",
                        message_id=None,
                    )
                    self.storage.increment_pushed_count(1)
                pushed += 1
                # 禮貌性微延遲，避免短時間大量連線觸發郵件主機連線頻率限制 (Rate Limit)
                await asyncio.sleep(0.5)
            else:
                logger.error(f"[錯誤] 推播 Email 至訂閱者 #{sub_id} ({to_addrs}) 失敗，中斷後續推播。")
                break

        # 處理溢出文章（防洗版合併摘要）
        if overflow_slice and pushed > 0:
            ov_msg = self._build_overflow_message(subscriber, overflow_slice, to_addrs, scope_label)
            ov_ok = await asyncio.to_thread(self._sync_send, to_addrs, ov_msg)
            if ov_ok and self.storage and sub_id is not None:
                for it in overflow_slice:
                    self.storage.record_delivery(
                        subscriber_id=sub_id,
                        article=it.get("article", {}),
                        channel_id="email",
                        message_id=None,
                    )

        return pushed

    async def update_sold(
        self,
        subscriber: Dict[str, Any],
        delivery_record: Dict[str, Any],
        detail: Dict[str, Any],
    ) -> bool:
        """
        Email 協議為單向非同步遞送機制，已送達使用者信箱之郵件無法於收件端直接原處篡改更新。
        依據架構契約明確回傳 False，並記錄除錯日誌。
        """
        logger.debug(
            f"[資訊] Email 不支援已送出信件之原位修改 (Article: {delivery_record.get('article_url')})，略過更新。"
        )
        return False
