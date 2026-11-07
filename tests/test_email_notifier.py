"""
單元測試：Email (SMTP) 郵件推播通知模組 (test_email_notifier.py)

100% 離線 Mock 測試保證：
- 不發起任何真實對外 SMTP 連線或網路封包
- 完整模擬 smtplib.SMTP、smtplib.SMTP_SSL、認證錯誤、連線超時與退信例外
- 驗證 MIME multipart/alternative 結構、UTF-8 標頭編碼與 HTML/XSS 防禦跳脫
"""

import asyncio
import email
import html
import os
import socket
import smtplib
import sys
import unittest
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

# Ensure repo root and isolated virtualenv site-packages are in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

VENV_SITE = "/tmp/test_venv/lib/python3.13/site-packages"
if os.path.exists(VENV_SITE) and VENV_SITE not in sys.path:
    sys.path.insert(0, VENV_SITE)

# 匯入待測模組 (支援由本機或專案根目錄載入)
from email_notifier import (
    EmailNotificationProvider,
    clean_header_value,
    parse_recipient_list,
)


class TestEmailRecipientAndHeaderParsing(unittest.TestCase):
    """測試收件者地址解析與標頭注入防禦"""

    def test_parse_recipient_list_various_formats(self):
        # 單一信箱
        self.assertEqual(parse_recipient_list("user@example.com"), ["user@example.com"])

        # 逗號與空格分隔多信箱
        self.assertEqual(
            parse_recipient_list("user1@example.com, user2@example.com, user3@example.com"),
            ["user1@example.com", "user2@example.com", "user3@example.com"],
        )

        # 分號與換行分隔
        self.assertEqual(
            parse_recipient_list("a@test.com; b@test.com\nc@test.com"),
            ["a@test.com", "b@test.com", "c@test.com"],
        )

        # "Name <addr>" 格式
        self.assertEqual(
            parse_recipient_list("Alice <alice@test.com>, Bob <bob@test.com>"),
            ["alice@test.com", "bob@test.com"],
        )

        # 重複信箱去重 (大小寫不敏感)
        self.assertEqual(
            parse_recipient_list("user@test.com, USER@test.com, user@test.com"),
            ["user@test.com"],
        )

        # 過濾無效信箱格式
        self.assertEqual(
            parse_recipient_list("invalid_email, good@test.com, missing_domain@, @nodomain"),
            ["good@test.com"],
        )

        # 空值與 None 處理
        self.assertEqual(parse_recipient_list(""), [])
        self.assertEqual(parse_recipient_list(None), [])
        self.assertEqual(parse_recipient_list([]), [])

    def test_clean_header_value(self):
        # 消除換行與製表符，防止 Header Injection
        raw = "Subject Line\r\nBcc: evil@hacker.com\n\tNew line"
        cleaned = clean_header_value(raw)
        self.assertNotIn("\r", cleaned)
        self.assertNotIn("\n", cleaned)
        self.assertNotIn("\t", cleaned)
        self.assertEqual(cleaned, "Subject Line Bcc: evil@hacker.com New line")
        self.assertEqual(clean_header_value(None), "")


class TestEmailNotificationProviderSyncSend(unittest.TestCase):
    """測試同步 SMTP 發送流程與例外防護 (100% Offline Mock)"""

    def setUp(self):
        self.provider = EmailNotificationProvider(
            host="smtp.gmail.com",
            port=587,
            user="bot@example.com",
            password="app_password_16_chars",
            use_tls=True,
            timeout=10.0,
        )
        self.msg = EmailMessage()
        self.msg["Subject"] = "Test Subject"
        self.msg["From"] = "bot@example.com"
        self.msg["To"] = "target@example.com"
        self.msg.set_content("Test body")

    @patch("smtplib.SMTP")
    def test_sync_send_starttls_success(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        ok = self.provider._sync_send(["target@example.com"], self.msg)
        self.assertTrue(ok)
        mock_smtp_cls.assert_called_with("smtp.gmail.com", 587, timeout=10.0)
        mock_server.ehlo.assert_called()
        mock_server.starttls.assert_called()
        mock_server.login.assert_called_with("bot@example.com", "app_password_16_chars")
        mock_server.send_message.assert_called_with(
            self.msg, from_addr="bot@example.com", to_addrs=["target@example.com"]
        )

    @patch("smtplib.SMTP_SSL")
    def test_sync_send_ssl_port_465(self, mock_ssl_cls):
        ssl_provider = EmailNotificationProvider(
            host="smtp.gmail.com",
            port=465,
            user="bot@example.com",
            password="app_password",
            timeout=8.0,
        )
        mock_server = MagicMock()
        mock_ssl_cls.return_value.__enter__.return_value = mock_server

        ok = ssl_provider._sync_send(["target@example.com"], self.msg)
        self.assertTrue(ok)
        mock_ssl_cls.assert_called()
        mock_server.login.assert_called_with("bot@example.com", "app_password")
        mock_server.send_message.assert_called_with(
            self.msg, from_addr="bot@example.com", to_addrs=["target@example.com"]
        )

    @patch("smtplib.SMTP")
    def test_sync_send_auth_error(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Invalid credentials")
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        ok = self.provider._sync_send(["target@example.com"], self.msg)
        self.assertFalse(ok)  # 安全攔截錯誤，回傳 False 不崩潰

    @patch("smtplib.SMTP")
    def test_sync_send_connect_error(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = smtplib.SMTPConnectError(421, b"Connection refused")

        ok = self.provider._sync_send(["target@example.com"], self.msg)
        self.assertFalse(ok)

    @patch("smtplib.SMTP")
    def test_sync_send_socket_timeout(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = socket.timeout("Timed out")

        ok = self.provider._sync_send(["target@example.com"], self.msg)
        self.assertFalse(ok)

    def test_sync_send_empty_recipients(self):
        ok = self.provider._sync_send([], self.msg)
        self.assertFalse(ok)


class TestEmailNotificationFormattingAndXSS(unittest.TestCase):
    """測試 MIME 郵件結構與 HTML/XSS 防禦過濾"""

    def setUp(self):
        self.provider = EmailNotificationProvider(
            host="smtp.gmail.com",
            port=587,
            user="bot@example.com",
            password="pwd",
            from_name="PTT 追蹤機器人",
        )

    def test_build_message_mime_multipart(self):
        item = {
            "article": {
                "board": "HardwareSale",
                "title": "[賣/台北] RTX 3060 12G 特價 NT$6,500",
                "author": "gamer",
                "url": "https://www.ptt.cc/bbs/HardwareSale/M.12345.html",
                "category": "賣",
                "date": "11/08",
                "nrec": "10",
            },
            "matched_keywords": ["3060", "RTX"],
            "detail": {
                "extracted_price": "NT$ 6,500",
                "extracted_location": "台北捷運市府站",
                "snippet": "品相九成新，原廠保固中。\n誠可小議。",
                "buyer_queue_count": 2,
                "ai_summary_note": "價格極具競爭力",
            },
        }

        msg = self.provider._build_single_article_message(
            subscriber={"id": 1, "scope": "user", "display_name": "測試用戶"},
            item=item,
            to_addrs=["user@example.com"],
            scope_label="[個人追蹤] 測試用戶",
        )

        self.assertTrue(msg.is_multipart())
        self.assertIn("HardwareSale", msg["Subject"])
        self.assertIn("RTX 3060", msg["Subject"])
        self.assertIn("6,500", msg["Subject"])
        self.assertEqual(msg["To"], "user@example.com")
        self.assertIn("PTT 追蹤機器人", msg["From"])

        # 檢驗包含 text/plain 與 text/html 雙重 Payload
        payload_types = [p.get_content_type() for p in msg.iter_parts()]
        self.assertIn("text/plain", payload_types)
        self.assertIn("text/html", payload_types)

        # 檢驗純文字部分
        text_part = next(p for p in msg.iter_parts() if p.get_content_type() == "text/plain")
        text_content = text_part.get_content()
        self.assertIn("欲售價格：NT$ 6,500", text_content)
        self.assertIn("3060", text_content)
        self.assertIn("品相九成新", text_content)

        # 檢驗 HTML 部分
        html_part = next(p for p in msg.iter_parts() if p.get_content_type() == "text/html")
        html_content = html_part.get_content()
        self.assertIn("NT$ 6,500", html_content)
        self.assertIn("HardwareSale", html_content)
        self.assertIn("https://www.ptt.cc/bbs/HardwareSale/M.12345.html", html_content)

    def test_strict_xss_html_injection_escaping(self):
        """驗證惡意文章標題或內文 HTML 標籤均被安全跳脫，防止 XSS"""
        malicious_item = {
            "article": {
                "board": "TestBoard",
                "title": '<script>alert("xss")</script><img src=x onerror=alert(1)>',
                "author": '<style>body{display:none;}</style>',
                "url": "https://www.ptt.cc/bbs/Test/M.1.html?param=<script>",
                "category": "<b>賣</b>",
                "date": "11/08",
                "nrec": "0",
            },
            "matched_keywords": ['"><script>alert(2)</script>'],
            "detail": {
                "extracted_price": '<span style="color:red">免費</span>',
                "extracted_location": '"><iframe src="javascript:alert(3)">',
                "snippet": '<script>malicious();</script><b>粗體</b> & "引號"',
                "buyer_queue_count": 0,
                "ai_summary_note": '<svg/onload=alert(4)>',
            },
        }

        msg = self.provider._build_single_article_message(
            subscriber={"id": 1, "scope": "user"},
            item=malicious_item,
            to_addrs=["user@example.com"],
            scope_label="[個人追蹤]",
        )

        html_part = next(p for p in msg.iter_parts() if p.get_content_type() == "text/html")
        html_content = html_part.get_content()

        # 絕對不能出現未被跳脫的原生惡意標籤
        self.assertNotIn('<script>alert("xss")</script>', html_content)
        self.assertNotIn("<img src=x onerror=alert(1)>", html_content)
        self.assertNotIn("<style>body{display:none;}</style>", html_content)
        self.assertNotIn('<iframe src="javascript:alert(3)">', html_content)
        self.assertNotIn("<svg/onload=alert(4)>", html_content)

        # 必須正確轉換為實體字元 (HTML Entity)
        self.assertIn("&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;", html_content)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html_content)

    def test_single_article_subject_crlf_sanitization(self):
        """驗證文章看板名稱或欄位含有換行注入時，主旨依然被安全過濾成單行"""
        item = {
            "article": {
                "board": "HardwareSale\r\nBcc: evil@attacker.com",
                "title": "Clean Title\nSubject: Injected",
                "author": "author",
                "url": "https://ptt.cc/test",
            },
            "detail": {"extracted_price": "1000\r\nTest"},
        }
        msg = self.provider._build_single_article_message(
            subscriber={"id": 1, "scope": "user", "display_name": "測試用戶"},
            item=item,
            to_addrs=["user@example.com"],
            scope_label="[個人追蹤] 測試用戶",
        )
        self.assertNotIn("\r", msg["Subject"])
        self.assertNotIn("\n", msg["Subject"])
        self.assertIn("HardwareSale Bcc: evil@attacker.com", msg["Subject"])


class TestEmailNotificationProviderAsyncDeliver(unittest.IsolatedAsyncioTestCase):
    """測試非同步推播 deliver 與 update_sold 流程"""

    async def asyncSetUp(self):
        self.storage = MagicMock()
        self.provider = EmailNotificationProvider(
            host="smtp.gmail.com",
            port=587,
            user="bot@example.com",
            password="pwd",
            default_to="fallback@test.com",
            storage=self.storage,
        )

    @patch("asyncio.sleep", return_value=None)
    async def test_deliver_single_recipient_success(self, mock_sleep):
        items = [
            {
                "article": {
                    "board": "HardwareSale",
                    "title": "RTX 4070 12G",
                    "author": "seller",
                    "url": "http://ptt.cc/1",
                },
                "matched_keywords": ["4070"],
                "detail": {"extracted_price": "18000"},
            }
        ]
        sub = {"id": 10, "scope": "user", "target_id": "alice@test.com"}

        with patch.object(self.provider, "_sync_send", return_value=True) as mock_send:
            pushed = await self.provider.deliver(sub, items)
            self.assertEqual(pushed, 1)
            mock_send.assert_called_once()
            args, _ = mock_send.call_args
            to_addrs, msg = args
            self.assertEqual(to_addrs, ["alice@test.com"])
            self.assertEqual(msg["To"], "alice@test.com")
            self.storage.record_delivery.assert_called_once_with(
                subscriber_id=10,
                article=items[0]["article"],
                channel_id="email",
                message_id=None,
            )
            self.storage.increment_pushed_count.assert_called_once_with(1)

    @patch("asyncio.sleep", return_value=None)
    async def test_deliver_multi_recipient_comma_separated(self, mock_sleep):
        items = [
            {
                "article": {
                    "board": "HardwareSale",
                    "title": "RTX 4080",
                    "author": "seller",
                    "url": "http://ptt.cc/2",
                },
                "matched_keywords": ["4080"],
            }
        ]
        sub = {"id": 11, "scope": "guild", "target_id": "alice@test.com, bob@test.com"}

        with patch.object(self.provider, "_sync_send", return_value=True) as mock_send:
            pushed = await self.provider.deliver(sub, items)
            self.assertEqual(pushed, 1)
            to_addrs, msg = mock_send.call_args[0]
            self.assertEqual(to_addrs, ["alice@test.com", "bob@test.com"])
            self.assertEqual(msg["To"], "alice@test.com, bob@test.com")

    async def test_deliver_unconfigured_returns_zero(self):
        unconfigured = EmailNotificationProvider(user="", password="")
        items = [{"article": {"board": "B", "title": "T"}}]
        sub = {"id": 1, "target_id": "test@test.com"}

        pushed = await unconfigured.deliver(sub, items)
        self.assertEqual(pushed, 0)

    @patch("asyncio.sleep", return_value=None)
    async def test_deliver_missing_target_id_uses_default(self, mock_sleep):
        items = [{"article": {"board": "B", "title": "T"}}]
        sub = {"id": 1, "target_id": ""}

        with patch.object(self.provider, "_sync_send", return_value=True) as mock_send:
            pushed = await self.provider.deliver(sub, items)
            self.assertEqual(pushed, 1)
            to_addrs = mock_send.call_args[0][0]
            self.assertEqual(to_addrs, ["fallback@test.com"])

    async def test_update_sold_returns_false(self):
        sub = {"id": 1, "target_id": "test@test.com"}
        delivery_record = {"article_url": "http://ptt.cc/sold"}
        detail = {"is_sold": True, "sold_reason": "已售出"}

        # Email 協議不支援原處覆寫，依約定回傳 False
        result = await self.provider.update_sold(sub, delivery_record, detail)
        self.assertFalse(result)

    @patch("asyncio.sleep", return_value=None)
    async def test_overflow_slice_summary_digest(self, mock_sleep):
        """驗證當命中文章超過 5 篇時，前 5 篇正常推播，溢出文章發送合併摘要郵件"""
        items = [
            {
                "article": {
                    "board": "HardwareSale",
                    "title": f"Item {i}",
                    "author": "seller",
                    "url": f"http://ptt.cc/{i}",
                },
                "detail": {"extracted_price": f"{i}000"},
            }
            for i in range(1, 8)
        ]
        sub = {"id": 20, "scope": "user", "target_id": "user@test.com"}

        with patch.object(self.provider, "_sync_send", return_value=True) as mock_send:
            pushed = await self.provider.deliver(sub, items)
            # 前 5 篇個別發送 + 1 封溢出合併摘要 = 呼叫 6 次 _sync_send
            self.assertEqual(pushed, 5)
            self.assertEqual(mock_send.call_count, 6)
            # 全部 7 篇均需登記在 delivery 記錄中以防重複追蹤
            self.assertEqual(self.storage.record_delivery.call_count, 7)


if __name__ == "__main__":
    unittest.main()
