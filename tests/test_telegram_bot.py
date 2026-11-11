"""
單元測試：Telegram 機器人與推播通知模組 (test_telegram_bot.py)

100% 離線 Mock 測試保證：
- 不發起任何真實對外 Telegram Bot API 連線或網路封包
- 完整模擬 TelegramAPIClient、HTTP 429 退避、HTTP 403 阻擋偵測
- 驗證 TelegramNotificationProvider 推播、售出卡片原地編輯、溢出處理
- 驗證 TelegramBot 指令路由、@username 去除與行內按鈕回呼處理
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure repo root and isolated virtualenv site-packages are in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

VENV_SITE = "/tmp/test_venv/lib/python3.13/site-packages"
if os.path.exists(VENV_SITE) and VENV_SITE not in sys.path:
    sys.path.insert(0, VENV_SITE)

from telegram_bot import (
    TelegramAPIClient,
    TelegramBot,
    TelegramNotificationProvider,
    build_article_keyboard,
    escape_tg,
    format_article_card,
)


class TestTelegramAPIClient(unittest.IsolatedAsyncioTestCase):
    """測試 TelegramAPIClient 核心請求與例外重試 (TEST-TG-01, 04, 05)"""

    async def asyncSetUp(self):
        self.client = TelegramAPIClient(token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")

    async def asyncTearDown(self):
        await self.client.close()

    async def test_get_me_success(self):
        """TEST-TG-01: getMe 成功驗證 Token"""
        mock_resp_data = {"ok": True, "result": {"id": 12345, "is_bot": True, "username": "PTTBot"}}
        with patch.object(self.client, "request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_resp_data
            res = await self.client.get_me()
            self.assertTrue(res["ok"])
            self.assertEqual(res["result"]["id"], 12345)
            self.assertEqual(res["result"]["username"], "PTTBot")
            mock_req.assert_called_once_with("getMe")

    async def test_send_message_payload(self):
        """TEST-TG-02: send_message 構造正確 payload"""
        with patch.object(self.client, "request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"ok": True, "result": {"message_id": 999}}
            keyboard = {"inline_keyboard": [[{"text": "Link", "url": "http://example.com"}]]}
            res = await self.client.send_message(
                chat_id="12345678",
                text="<b>Hello</b>",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            self.assertTrue(res["ok"])
            mock_req.assert_called_once_with(
                "sendMessage",
                {
                    "chat_id": "12345678",
                    "text": "<b>Hello</b>",
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                    "reply_markup": keyboard,
                },
            )

    async def test_edit_message_text_payload(self):
        """TEST-TG-03: edit_message_text 構造正確 payload"""
        with patch.object(self.client, "request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"ok": True, "result": {"message_id": 888}}
            res = await self.client.edit_message_text(
                chat_id="12345678",
                message_id=888,
                text="<s>[已售出]</s>",
                parse_mode="HTML",
            )
            self.assertTrue(res["ok"])
            mock_req.assert_called_once_with(
                "editMessageText",
                {
                    "chat_id": "12345678",
                    "message_id": 888,
                    "text": "<s>[已售出]</s>",
                    "parse_mode": "HTML",
                },
            )

    @patch("asyncio.sleep", return_value=None)
    async def test_rate_limit_backoff_retry(self, mock_sleep):
        """TEST-TG-04: HTTP 429 速率限制退避重試"""
        # 模擬 session.post 回應
        mock_resp_429 = AsyncMock()
        mock_resp_429.status = 429
        mock_resp_429.json = AsyncMock(return_value={"ok": False, "parameters": {"retry_after": 1}})

        mock_resp_200 = AsyncMock()
        mock_resp_200.status = 200
        mock_resp_200.json = AsyncMock(return_value={"ok": True, "result": {"message_id": 100}})

        mock_session = MagicMock()
        # post() 返回一個非同步上下文管理器
        mock_session.post.side_effect = [
            AsyncMock(__aenter__=AsyncMock(return_value=mock_resp_429), __aexit__=AsyncMock(return_value=None)),
            AsyncMock(__aenter__=AsyncMock(return_value=mock_resp_200), __aexit__=AsyncMock(return_value=None)),
        ]

        with patch.object(self.client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            res = await self.client.request("sendMessage", {"text": "hi"})
            self.assertTrue(res["ok"])
            self.assertEqual(res["result"]["message_id"], 100)
            mock_sleep.assert_called_once_with(2.0)  # retry_after (1) + 1.0

    @patch("asyncio.sleep", return_value=None)
    async def test_rate_limit_backoff_retry_null_parameters(self, mock_sleep):
        """TEST-TG-04B: HTTP 429 回應中 parameters 為 null 時防禦處理"""
        mock_resp_429 = AsyncMock()
        mock_resp_429.status = 429
        mock_resp_429.json = AsyncMock(return_value={"ok": False, "parameters": None})

        mock_resp_200 = AsyncMock()
        mock_resp_200.status = 200
        mock_resp_200.json = AsyncMock(return_value={"ok": True, "result": {"message_id": 101}})

        mock_session = MagicMock()
        mock_session.post.side_effect = [
            AsyncMock(__aenter__=AsyncMock(return_value=mock_resp_429), __aexit__=AsyncMock(return_value=None)),
            AsyncMock(__aenter__=AsyncMock(return_value=mock_resp_200), __aexit__=AsyncMock(return_value=None)),
        ]

        with patch.object(self.client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            res = await self.client.request("sendMessage", {"text": "hi"})
            self.assertTrue(res["ok"])
            mock_sleep.assert_called_once_with(4.0)  # default 3 + 1.0

    async def test_http_403_forbidden_handling(self):
        """TEST-TG-05: HTTP 403 用戶阻擋偵測"""
        mock_resp_403 = AsyncMock()
        mock_resp_403.status = 403
        mock_resp_403.json = AsyncMock(
            return_value={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}
        )

        mock_session = MagicMock()
        mock_session.post.return_value = AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp_403),
            __aexit__=AsyncMock(return_value=None),
        )

        with patch.object(self.client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            res = await self.client.request("sendMessage", {"text": "hi"})
            self.assertFalse(res["ok"])
            self.assertEqual(res["error_code"], 403)
            self.assertIn("blocked", res["description"])


class TestTelegramFormattingAndSecurity(unittest.TestCase):
    """測試 Telegram HTML 轉義與長度截斷防護 (TEST-TG-06, 07)"""

    def test_html_xss_escaping(self):
        """TEST-TG-06: HTML 標籤與 XSS 字元轉義"""
        article = {
            "board": "HardwareSale",
            "title": "[賣/台北] RTX 3060 < 7000元 & 贈品",
            "author": "tester<script>",
            "category": "賣",
            "url": "https://www.ptt.cc/bbs/HardwareSale/M.1.html",
        }
        detail = {
            "extracted_price": "<$6,500>",
            "content_snippet": '測試內文含 <img src="x" onerror="alert(1)"> 標籤 & 符號',
            "ai_summary_note": "<script>alert(2)</script>",
        }
        card = format_article_card(article, matched_keywords=["3060", "<script>"], detail=detail)

        # 絕對不可包含未轉義之 HTML 標籤
        self.assertNotIn("<script>", card)
        self.assertNotIn("<img", card)
        self.assertNotIn("<$6,500>", card)

        # 必須正確轉義為實體字符
        self.assertIn("&lt;script&gt;", card)
        self.assertIn("&amp;", card)
        self.assertIn("&lt;$6,500&gt;", card)
        self.assertIn("3060", card)

    def test_message_length_cutoff_4000(self):
        """TEST-TG-07: 超長文章 4,000 字元長度截斷"""
        long_content = "超長內文測試 " * 800  # > 4800 字
        article = {
            "board": "HardwareSale",
            "title": "長篇測試文章",
            "author": "tester",
            "url": "https://www.ptt.cc/bbs/HardwareSale/M.long.html",
        }
        detail = {"snippet": long_content}
        card = format_article_card(article, detail=detail)

        self.assertLessEqual(len(card), 4000)
        self.assertIn("點此閱讀全文", card)


class TestTelegramNotificationProvider(unittest.IsolatedAsyncioTestCase):
    """測試 TelegramNotificationProvider 推播與售出原地更新 (TEST-TG-08, 09, 10)"""

    async def asyncSetUp(self):
        self.mock_api = MagicMock(spec=TelegramAPIClient)
        self.mock_storage = MagicMock()
        self.provider = TelegramNotificationProvider(
            api_client=self.mock_api,
            storage=self.mock_storage,
        )

    @patch("asyncio.sleep", return_value=None)
    async def test_deliver_success(self, mock_sleep):
        """TEST-TG-08: deliver 推播成功並記錄於 storage"""
        items = [
            {
                "article": {
                    "board": "HardwareSale",
                    "title": "RTX 3070",
                    "author": "seller",
                    "url": "http://ptt.cc/1",
                },
                "matched_keywords": ["3070"],
                "detail": {"extracted_price": "8000"},
            },
            {
                "article": {
                    "board": "HardwareSale",
                    "title": "RTX 3080",
                    "author": "seller2",
                    "url": "http://ptt.cc/2",
                },
                "matched_keywords": ["3080"],
                "detail": {"extracted_price": "12000"},
            },
        ]
        sub = {
            "id": 10,
            "scope": "user",
            "target_id": "987654321",
            "display_name": "TGUser",
            "enabled": 1,
            "dm_failed": 0,
        }

        self.mock_api.send_message = AsyncMock(
            side_effect=[
                {"ok": True, "result": {"message_id": 101}},
                {"ok": True, "result": {"message_id": 102}},
            ]
        )

        pushed = await self.provider.deliver(sub, items)
        self.assertEqual(pushed, 2)
        self.assertEqual(self.mock_api.send_message.call_count, 2)
        self.assertEqual(self.mock_storage.record_delivery.call_count, 2)
        self.assertEqual(self.mock_storage.increment_pushed_count.call_count, 2)

    @patch("asyncio.sleep", return_value=None)
    async def test_deliver_user_blocked_marks_failed(self, mock_sleep):
        """TEST-TG-09: 用戶阻擋 (403) 自動標記 dm_failed 與停用"""
        items = [
            {
                "article": {
                    "board": "HardwareSale",
                    "title": "RTX 4090",
                    "author": "rich",
                    "url": "http://ptt.cc/4090",
                }
            }
        ]
        sub = {
            "id": 12,
            "scope": "user",
            "target_id": "telegram_112233",
            "enabled": 1,
            "dm_failed": 0,
        }

        self.mock_api.send_message = AsyncMock(
            return_value={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}
        )

        pushed = await self.provider.deliver(sub, items)
        self.assertEqual(pushed, 0)
        self.mock_storage.mark_subscriber_dm_failed.assert_called_once_with(12, True)
        self.mock_storage.set_subscriber_enabled.assert_called_once_with(12, False)

    async def test_update_sold_success(self):
        """TEST-TG-10: update_sold 原處編輯訊息"""
        sub = {"id": 10}
        delivery_record = {
            "channel_id": "123456",
            "message_id": "789",
            "article_url": "http://ptt.cc/item",
            "board": "HardwareSale",
            "title": "RTX 3070 售出",
            "author": "seller",
        }
        detail = {"extracted_price": "8000", "is_sold": True}

        self.mock_api.edit_message_text = AsyncMock(
            return_value={"ok": True, "result": {"message_id": 789}}
        )

        res = await self.provider.update_sold(sub, delivery_record, detail)
        self.assertTrue(res)
        self.mock_api.edit_message_text.assert_called_once()
        args, kwargs = self.mock_api.edit_message_text.call_args
        self.assertEqual(kwargs.get("chat_id") or args[0], "123456")
        self.assertEqual(kwargs.get("message_id") or args[1], 789)
        self.assertIn("已售出", kwargs.get("text") or args[2])


class TestTelegramBotCommandsAndCallbacks(unittest.IsolatedAsyncioTestCase):
    """測試 TelegramBot 雙向指令與按鈕回呼處理 (TEST-TG-11, 12)"""

    async def asyncSetUp(self):
        self.mock_api = MagicMock(spec=TelegramAPIClient)
        self.mock_api.send_message = AsyncMock(return_value={"ok": True})
        self.mock_api.answer_callback_query = AsyncMock(return_value={"ok": True})
        self.mock_storage = MagicMock()
        self.bot = TelegramBot(
            token="dummy_token",
            storage=self.mock_storage,
            api_client=self.mock_api,
        )

    async def test_command_start_and_help(self):
        """TEST-TG-11: 測試 /start 與 /help 指令"""
        self.mock_storage.get_or_create_subscriber.return_value = {
            "id": 1,
            "scope": "user",
            "enabled": 1,
        }

        # 模擬 /start
        msg_start = {
            "message_id": 1,
            "chat": {"id": 12345, "type": "private"},
            "from": {"id": 12345, "first_name": "Alice"},
            "text": "/start",
        }
        await self.bot._handle_message(msg_start)
        self.mock_api.send_message.assert_called()
        self.assertIn("歡迎使用 PTT 智慧追蹤機器人", self.mock_api.send_message.call_args[0][1])

        # 模擬 /help@MyBot (驗證 @bot_username 去除)
        msg_help = {
            "message_id": 2,
            "chat": {"id": 12345, "type": "private"},
            "from": {"id": 12345, "first_name": "Alice"},
            "text": "/help@MyBot",
        }
        await self.bot._handle_message(msg_help)
        self.assertIn("操作手冊", self.mock_api.send_message.call_args[0][1])

    async def test_command_keyword_add_and_list(self):
        """TEST-TG-11: 測試 /keyword_add 與 /keyword_list"""
        self.mock_storage.get_or_create_subscriber.return_value = {
            "id": 5,
            "scope": "user",
            "enabled": 1,
        }
        self.mock_storage.add_keyword.return_value = (True, "已新增關鍵字 3060Ti")

        # 執行 /keyword_add 3060Ti 8000
        msg_add = {
            "message_id": 3,
            "chat": {"id": 12345, "type": "private"},
            "text": "/keyword_add 3060Ti 8000",
        }
        await self.bot._handle_message(msg_add)
        self.mock_storage.add_keyword.assert_called_with(
            keyword="3060Ti",
            board="HardwareSale",
            category="賣",
            subscriber_id=5,
            max_price=8000,
        )
        self.assertIn("成功", self.mock_api.send_message.call_args[0][1])

        # 執行 /keyword_list
        self.mock_storage.get_keywords.return_value = [
            {"id": 101, "board": "HardwareSale", "category": "賣", "keyword": "3060Ti", "max_price": 8000}
        ]
        msg_list = {
            "message_id": 4,
            "chat": {"id": 12345, "type": "private"},
            "text": "/keyword_list",
        }
        await self.bot._handle_message(msg_list)
        args, kwargs = self.mock_api.send_message.call_args
        self.assertIn("3060Ti", args[1])
        self.assertIn("reply_markup", kwargs)

    async def test_callback_query_delete_keyword(self):
        """TEST-TG-12: 行內按鈕回呼刪除規則 (del_kw:<id>)"""
        self.mock_storage.get_or_create_subscriber.return_value = {"id": 5}
        self.mock_storage.remove_keyword.return_value = {"id": 101, "keyword": "3060Ti"}
        self.mock_storage.get_keywords.return_value = []

        cb = {
            "id": "cb_123",
            "data": "del_kw:101",
            "message": {"chat": {"id": 12345, "type": "private"}},
            "from": {"id": 12345, "first_name": "Alice"},
        }
        await self.bot._handle_callback_query(cb)

        self.mock_storage.remove_keyword.assert_called_with("101", subscriber_id=5)
        self.mock_api.answer_callback_query.assert_called_once()
        self.assertIn("已刪除", self.mock_api.answer_callback_query.call_args[1]["text"])

    async def test_handle_message_null_text_no_crash(self):
        """TEST-TG-13: message.text 為 null/None 時防禦不拋出例外"""
        msg_none = {
            "message_id": 99,
            "chat": {"id": 12345, "type": "private"},
            "text": None,
        }
        await self.bot._handle_message(msg_none)
        self.mock_api.send_message.assert_not_called()

    async def test_handle_message_missing_or_invalid_chat_dict(self):
        """TEST-TG-14: message 缺少 chat dict 或格式無效時安全返回"""
        for malformed_chat in [None, {}, "invalid_chat", {"id": None}]:
            msg_bad = {
                "message_id": 100,
                "chat": malformed_chat,
                "text": "/start",
            }
            await self.bot._handle_message(msg_bad)
            self.mock_api.send_message.assert_not_called()

    async def test_callback_query_chat_none_defensive_rejection(self):
        """TEST-TG-15: callback_query 之 message.chat 為 null/None 時防禦處理"""
        cb = {
            "id": "cb_no_chat",
            "data": "del_kw:101",
            "message": {"chat": None},
            "from": {"id": 12345, "first_name": "Alice"},
        }
        await self.bot._handle_callback_query(cb)
        self.mock_api.answer_callback_query.assert_called_once_with(
            "cb_no_chat", text="[錯誤] 無效操作"
        )

    async def test_polling_loop_skip_missing_or_invalid_update_id(self):
        """TEST-TG-16: polling 迴圈遇到缺少 update_id 或非字典更新時跳過並繼續執行"""
        malformed_updates = [
            "not_a_dict",
            {"missing_update_id": True},
            {"update_id": None},
            {"update_id": 200, "message": {"chat": {"id": 12345}, "text": "/help"}},
        ]
        call_count = 0

        async def fake_get_updates(offset=None, timeout=20):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return malformed_updates
            self.bot._running = False
            return []

        self.mock_api.get_updates = AsyncMock(side_effect=fake_get_updates)

        with patch("asyncio.sleep", return_value=None):
            await self.bot.start_polling()

        self.assertEqual(self.bot.last_update_id, 201)
        self.mock_api.send_message.assert_called_once()
        self.assertIn("操作手冊", self.mock_api.send_message.call_args[0][1])


class TestConfigAndMultiPlatformDeliveryWiring(unittest.IsolatedAsyncioTestCase):
    """測試組態安全轉換、多平台分發器自動掛載與生命週期釋放"""

    def test_config_safe_helpers(self):
        import config

        # 測試 _parse_bool
        self.assertTrue(config._parse_bool("true"))
        self.assertTrue(config._parse_bool("1"))
        self.assertTrue(config._parse_bool("yes"))
        self.assertTrue(config._parse_bool("ON"))
        self.assertTrue(config._parse_bool("enabled"))
        self.assertFalse(config._parse_bool("false"))
        self.assertFalse(config._parse_bool("0"))
        self.assertFalse(config._parse_bool("no"))
        self.assertFalse(config._parse_bool("off"))
        self.assertFalse(config._parse_bool("disabled"))
        self.assertTrue(config._parse_bool(None, default=True))
        self.assertFalse(config._parse_bool(None, default=False))
        self.assertEqual(config._parse_bool("unknown", default=True), True)

        # 測試 _parse_int
        self.assertEqual(config._parse_int("587", 25), 587)
        self.assertEqual(config._parse_int("not_a_number", 587), 587)
        self.assertEqual(config._parse_int(None, 587), 587)

    async def test_delivery_dispatcher_and_manager_close(self):
        from delivery import DeliveryDispatcher, DeliveryManager

        dispatcher = DeliveryDispatcher(storage=None)
        mock_provider_coro = MagicMock()
        mock_provider_coro.close = AsyncMock()
        mock_provider_sync = MagicMock()
        mock_provider_sync.close = MagicMock()

        dispatcher.register_provider("p1", mock_provider_coro)
        dispatcher.register_provider("p2", mock_provider_sync)

        await dispatcher.close()
        mock_provider_coro.close.assert_awaited_once()
        mock_provider_sync.close.assert_called_once()

        # Manager close
        manager = DeliveryManager(bot=None, storage=None, dispatcher=dispatcher, auto_register_providers=False)
        with patch.object(dispatcher, "close", new_callable=AsyncMock) as mock_disp_close:
            await manager.close()
            mock_disp_close.assert_awaited_once()

    def test_delivery_manager_auto_registration_telegram_and_email(self):
        import config
        from delivery import DeliveryManager

        mock_bot = MagicMock()
        mock_storage = MagicMock()

        with patch.object(config, "ENABLE_TELEGRAM", True), \
             patch.object(config, "TELEGRAM_BOT_TOKEN", "123:test_token"), \
             patch.object(config, "ENABLE_EMAIL", True), \
             patch.object(config, "SMTP_HOST", "smtp.test.com"):
            manager = DeliveryManager(
                bot=mock_bot,
                storage=mock_storage,
                auto_register_providers=True,
            )
            self.assertIn("discord", manager.dispatcher._providers)
            self.assertIn("telegram", manager.dispatcher._providers)
            self.assertIn("email", manager.dispatcher._providers)

    async def test_bot_telegram_lifecycle_hooks(self):
        import config
        from bot import PTTDiscordBot

        mock_storage = MagicMock()
        mock_crawler = MagicMock()
        mock_crawler.close = AsyncMock()
        mock_scheduler = MagicMock()
        mock_agent = MagicMock()
        mock_agent.close = AsyncMock()

        with patch.object(PTTDiscordBot, "_polling_loop", new_callable=AsyncMock):
            bot_inst = PTTDiscordBot(
                storage=mock_storage,
                crawler=mock_crawler,
                scheduler=mock_scheduler,
                agent=mock_agent,
            )
            bot_inst.add_view = MagicMock()

            # 測試 setup_hook 在 ENABLE_TELEGRAM=True 時啟動長輪詢背景任務
            with patch.object(config, "ENABLE_TELEGRAM", True), \
                 patch.object(config, "TELEGRAM_BOT_TOKEN", "123:test_token"), \
                 patch("telegram_bot.TelegramBot") as mock_tg_bot_cls:
                mock_tg_instance = MagicMock()
                mock_tg_instance.initialize = AsyncMock(return_value=True)
                mock_tg_instance.start_polling = AsyncMock()
                mock_tg_instance.stop = AsyncMock()
                mock_tg_bot_cls.return_value = mock_tg_instance

                await bot_inst.setup_hook()
                self.assertIsNotNone(bot_inst.tg_bot)
                self.assertIsNotNone(bot_inst._tg_task)

                # 測試 close 時正確停止 Telegram 機器人與取消任務
                with patch("discord.Client.close", new_callable=AsyncMock):
                    await bot_inst.close()
                    mock_tg_instance.stop.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
