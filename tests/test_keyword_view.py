"""
tests/test_keyword_view.py - Tests for apply_keyword_view, pick_item_token,
and regression tests for P0 fixes (blacklist removal, GroqScheduler rate limit/busy errors,
and delivery/telegram overflow chunking).
"""

import asyncio
import tempfile
import time
import unittest
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

from config import MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER
from delivery import DeliveryManager
from groq_scheduler import (
    P0_INTERACTIVE,
    P2_BATCH_SCAN,
    GroqBusyError,
    GroqScheduler,
    ScheduledGroqJob,
)
from ptt_crawler import PTTCrawler, apply_keyword_view
from sold_rules import AVAILABLE, SOLD, pick_item_token
from storage import BotStorage
from telegram_bot import TelegramNotificationProvider


class TestKeywordViewAndP0Regressions(unittest.TestCase):
    """
    Phase 1.3: apply_keyword_view and P0 regression suite.
    """

    def test_pick_item_token_order_invariance(self):
        """
        pick_item_token deterministically extracts the hardware model token
        regardless of positive token order.
        """
        self.assertEqual(pick_item_token(["3060ti", "台北"]), "3060ti")
        self.assertEqual(pick_item_token(["台北", "3060ti"]), "3060ti")
        self.assertEqual(pick_item_token(["高雄", "面交", "rx6700xt"]), "rx6700xt")
        self.assertEqual(pick_item_token(["rx6700xt", "高雄", "面交"]), "rx6700xt")

    def test_apply_keyword_view_exclusion_token_selection(self):
        """
        apply_keyword_view with '3060 -ti' on multi-item post
        ('1. RTX 3060Ti 5800' / '2. RTX 3060 12G 5000') selects line 2 (price 5000).
        """
        base_detail = {
            "title": "[賣/台北] 多張顯卡換現出清",
            "url": "https://www.ptt.cc/bbs/HardwareSale/M.1234567.A.111.html",
            "raw_price_lines": ["1. RTX 3060Ti 5800", "2. RTX 3060 12G 5000"],
            "body_only": "1. RTX 3060Ti 5800\n2. RTX 3060 12G 5000",
            "is_sold": False,
        }
        view = apply_keyword_view(base_detail, "3060 -ti")
        self.assertIn("5000", view.get("extracted_price", ""))
        self.assertNotIn("5800", view.get("extracted_price", ""))

    def test_apply_keyword_view_token_order_identical_results(self):
        """
        apply_keyword_view with '3060ti 台北' and '台北 3060ti' produces identical results.
        """
        base_detail = {
            "title": "[賣/台北] 售 3060ti 還有其他零件",
            "url": "https://www.ptt.cc/bbs/HardwareSale/M.1234567.A.222.html",
            "raw_price_lines": ["RTX 3060Ti 6000", "B560M 2000"],
            "body_only": "RTX 3060Ti 6000\nB560M 2000",
            "is_sold": False,
        }
        view_a = apply_keyword_view(base_detail, "3060ti 台北")
        view_b = apply_keyword_view(base_detail, "台北 3060ti")
        self.assertEqual(view_a, view_b)

    def test_fullwidth_list_markers_sold_status_isolation(self):
        """
        Fullwidth list markers:
        '１． GTX 1060（已售出）' / '２． RTX 3060Ti $5800'
        Tracking 3060Ti is AVAILABLE, tracking 1060 is SOLD.
        """
        base_detail = {
            "title": "[賣/雙北] 顯卡合售",
            "url": "https://www.ptt.cc/bbs/HardwareSale/M.1234567.A.333.html",
            "raw_price_lines": [
                "１． GTX 1060（已售出）",
                "２． RTX 3060Ti $5800",
            ],
            "body_only": (
                "１． GTX 1060（已售出）\n"
                "２． RTX 3060Ti $5800\n"
                "面交自取優先"
            ),
            "is_sold": False,
        }
        view_3060ti = apply_keyword_view(base_detail, "3060Ti")
        self.assertFalse(view_3060ti.get("is_sold"))
        self.assertEqual(view_3060ti.get("sold_state"), AVAILABLE)

        view_1060 = apply_keyword_view(base_detail, "1060")
        self.assertTrue(view_1060.get("is_sold"))
        self.assertEqual(view_1060.get("sold_state"), SOLD)

    def test_apply_keyword_view_deep_copy_isolation(self):
        """
        Mutating list fields on the dict returned by apply_keyword_view
        must NOT mutate base_detail.
        """
        base_detail = {
            "title": "[賣/台北] 測試文章",
            "recent_pushes": [{"user": "user1", "content": "push1"}],
            "author_notes": ["note1"],
            "raw_price_lines": ["price1"],
            "is_sold": False,
        }
        view = apply_keyword_view(base_detail, "測試")

        # Mutate lists on returned view
        view["recent_pushes"].append({"user": "user2", "content": "push2"})
        view["author_notes"].append("note2")
        view["raw_price_lines"].append("price2")

        # Verify base_detail remains unmodified
        self.assertEqual(len(base_detail["recent_pushes"]), 1)
        self.assertEqual(len(base_detail["author_notes"]), 1)
        self.assertEqual(len(base_detail["raw_price_lines"]), 1)

    def test_p0_regression_storage_remove_blacklist(self):
        """
        Regression test: storage.remove_blacklist properly removes a blacklisted user
        (is_blacklisted becomes False).
        """
        with tempfile.NamedTemporaryFile() as tf:
            storage = BotStorage(tf.name)
            target_user = "123456789012345678"

            self.assertFalse(storage.is_blacklisted(target_user))
            storage.set_blacklist(target_user, reason="違反使用規範")
            self.assertTrue(storage.is_blacklisted(target_user))

            removed = storage.remove_blacklist(target_user)
            self.assertTrue(removed)
            self.assertFalse(storage.is_blacklisted(target_user))

            # Removing a non-existent user returns False
            self.assertFalse(storage.remove_blacklist(target_user))

    def test_p0_regression_groq_scheduler_busy_error_and_rate_limit(self):
        """
        Regression test:
        1. GroqScheduler raises GroqBusyError when a P2 job is submitted at >= 70% queue capacity.
        2. GroqScheduler raises GroqBusyError when all models are on cooldown past deadline
           (does NOT bypass rate limits).
        """
        async def _test():
            # 1. Test >= 70% queue capacity rejects P2 tasks
            sched = GroqScheduler(api_key="fake_groq_key")
            await sched.close()
            sched._queue = asyncio.PriorityQueue(maxsize=10)

            dummy_payload = {
                "messages": [{"role": "user", "content": "hi"}],
                "tools": None,
                "temperature": 0.2,
                "max_tokens": 100,
                "json_mode": False,
            }
            # Fill queue to 7 items (70% of 10)
            futures = []
            for i in range(7):
                fut = asyncio.get_running_loop().create_future()
                futures.append(fut)
                dummy_job = ScheduledGroqJob(
                    priority=P0_INTERACTIVE,
                    seq=i,
                    payload=dummy_payload,
                    allowed_models=["m1"],
                    est_tokens=100,
                    future=fut,
                )
                sched._queue.put_nowait(dummy_job)

            with self.assertRaises(GroqBusyError) as ctx:
                await sched.submit_chat(
                    [{"role": "user", "content": "scan"}],
                    priority=P2_BATCH_SCAN,
                )
            self.assertIn("70%", str(ctx.exception))
            for f in futures:
                if not f.done():
                    f.cancel()

            # 2. Test cooldown past deadline raises GroqBusyError
            sched2 = GroqScheduler(api_key="fake_groq_key")
            await sched2.close()
            now_ts = time.time()
            for b in sched2.buckets.values():
                b.cooldown_until = now_ts + 9999.0

            fut2 = asyncio.get_running_loop().create_future()
            job = ScheduledGroqJob(
                priority=P0_INTERACTIVE,
                seq=1,
                payload=dummy_payload,
                allowed_models=list(sched2.buckets.keys()),
                est_tokens=100,
                future=fut2,
            )

            t = now_ts
            def fake_time():
                nonlocal t
                t += 35.0
                return t

            with mock.patch("time.time", side_effect=fake_time):
                with self.assertRaises(GroqBusyError) as ctx2:
                    await sched2._execute_job(job)
                self.assertIn("冷卻或忙碌中", str(ctx2.exception))

        asyncio.run(_test())

    def test_p0_regression_delivery_and_telegram_overflow_chunking(self):
        """
        Regression test:
        delivery.py and telegram_bot.py overflow slice with > 10 overflow items
        chunks into multiple summary messages and records all delivered items
        without dropping item 11+.
        """
        async def _test():
            with tempfile.NamedTemporaryFile() as tf:
                storage = BotStorage(tf.name)

                # Total 30 items: MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER (5) primary + 25 overflow
                # 25 overflow items chunk into: chunk1 (10), chunk2 (10), chunk3 (5) -> 3 chunks
                total_items = MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER + 25
                test_items = []
                for i in range(total_items):
                    test_items.append(
                        {
                            "article": {
                                "id": f"art_overflow_{i}",
                                "title": f"出清零件 品項 #{i}",
                                "url": f"https://www.ptt.cc/bbs/HardwareSale/M.overflow.{i}.html",
                                "board": "HardwareSale",
                                "author": f"seller_{i}",
                                "date": "11/10",
                                "nrec": "10",
                            },
                            "matched_keywords": ["出清"],
                            "detail": {"extracted_price": f"{1000 + i}"},
                        }
                    )

                # 1. Test Discord DeliveryManager
                sub_discord = storage.add_subscriber(
                    scope="guild", discord_id="999888777", push_channel_id="111222333"
                )
                bot_mock = MagicMock()
                channel_mock = MagicMock()
                channel_mock.id = 111222333
                channel_mock.guild = MagicMock(id=999888777)
                msg_mock = MagicMock(id=123456)
                channel_mock.send = AsyncMock(return_value=msg_mock)
                bot_mock.get_channel.return_value = channel_mock

                delivery_mgr = DeliveryManager(bot=bot_mock, storage=storage)
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    await delivery_mgr.deliver_subscriber_matches(sub_discord, test_items)

                # Expect 5 primary + 3 overflow chunks = 8 send calls
                expected_sends = MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER + 3
                self.assertEqual(channel_mock.send.call_count, expected_sends)

                # Verify all 30 deliveries recorded in database
                row_dc = storage._conn.execute(
                    "SELECT COUNT(*) FROM deliveries WHERE subscriber_id = ?",
                    (sub_discord["id"],),
                ).fetchone()[0]
                self.assertEqual(
                    row_dc,
                    total_items,
                    f"Discord deliveries expected {total_items}, got {row_dc} (items dropped!)",
                )

                # 2. Test TelegramNotificationProvider
                sub_tg = storage.add_subscriber(
                    scope="user", platform="telegram", target_id="tg_123456"
                )
                tg_provider = TelegramNotificationProvider(token="test_token", storage=storage)
                tg_provider.api = AsyncMock()
                tg_provider.api.send_message = AsyncMock(return_value={"ok": True})

                with patch("asyncio.sleep", new_callable=AsyncMock):
                    await tg_provider.deliver(sub_tg, test_items)

                # Expect 5 primary + 3 overflow summary chunks = 8 send_message calls
                self.assertEqual(tg_provider.api.send_message.call_count, expected_sends)

                # Verify all 30 deliveries recorded in database
                row_tg = storage._conn.execute(
                    "SELECT COUNT(*) FROM deliveries WHERE subscriber_id = ?",
                    (sub_tg["id"],),
                ).fetchone()[0]
                self.assertEqual(
                    row_tg,
                    total_items,
                    f"Telegram deliveries expected {total_items}, got {row_tg} (items dropped!)",
                )

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
