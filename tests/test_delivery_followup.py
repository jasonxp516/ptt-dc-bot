import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
VENV_SITE = "/tmp/ptt_venv/lib/python3.13/site-packages"
if os.path.exists(VENV_SITE) and VENV_SITE not in sys.path:
    sys.path.insert(0, VENV_SITE)

import discord

from delivery import (
    BaseNotificationProvider,
    DeliveryManager,
    DiscordNotificationProvider,
)
from embeds import build_article_embed, update_existing_embed_for_sold
from storage import BotStorage, compute_dedupe_hash
from telegram_bot import TelegramNotificationProvider


ARTICLE_URL = "https://www.ptt.cc/bbs/HardwareSale/M.1234567890.A.ABC123.html"


def article(url=ARTICLE_URL, title="[賣] 顯卡"):
    return {
        "url": url,
        "board": "HardwareSale",
        "title": title,
        "author": "seller",
        "date": "11/10",
        "category": "賣",
        "nrec": "3",
    }


def sold_detail(state="SOLD"):
    return {
        "is_sold": True,
        "sold_state": state,
        "extracted_price": "5000 元",
        "buyer_queue_count": 0,
        "sold_reason": "交易完成",
    }


class RecordingProvider(BaseNotificationProvider):
    platform_name = "fake"

    def __init__(self, result=True):
        self.result = result
        self.calls = []

    async def deliver(self, subscriber, items):
        return len(items)

    async def update_sold(self, subscriber, delivery_record, detail):
        self.calls.append((subscriber, delivery_record, detail))
        return self.result


class DetailCrawler:
    def __init__(self, detail):
        self.detail = detail

    def is_in_cooldown(self):
        return False, 0

    async def fetch_article_full_detail(self, *args, **kwargs):
        return dict(self.detail)


class FakeMessage:
    def __init__(self, embed=None, error=None):
        self.embeds = [embed] if embed else []
        self.error = error
        self.edited_embed = None

    async def edit(self, *, embed):
        if self.error:
            raise self.error
        self.edited_embed = embed


class FakeChannel:
    def __init__(self, messages):
        self.id = 456
        self.messages = messages

    async def fetch_message(self, message_id):
        return self.messages[int(message_id)]


class FakeBot:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, channel_id):
        return self.channel if channel_id == self.channel.id else None

    def get_user(self, user_id):
        user = MagicMock()
        user.dm_channel = self.channel
        return user


class TestDeliveryFollowup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = BotStorage(os.path.join(self.tmp.name, "bot.db"))

    async def asyncTearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def add_subscriber(self, platform="discord", target_id="123"):
        return self.storage.add_subscriber(
            scope="user",
            platform=platform,
            target_id=target_id,
            display_name=target_id,
        )

    def add_delivery(self, subscriber_id, url=ARTICLE_URL, keyword="3060", message_id="1"):
        self.storage.record_delivery(
            subscriber_id,
            article(url=url),
            channel_id="456",
            message_id=message_id,
            matched_keyword=keyword,
        )
        return self.storage.get_recent_unmarked_deliveries(limit=20)[-1]

    async def test_multi_item_sold_followup_only_marks_matching_subscriber(self):
        first = self.add_subscriber(target_id="1001")
        second = self.add_subscriber(target_id="1002")
        self.storage.record_delivery(first["id"], article(), "456", "1", "3060")
        self.storage.record_delivery(second["id"], article(), "456", "2", "3070")

        embed1 = build_article_embed(article(), ["3060"], {"extracted_price": "1. RTX 3060 5000"})
        embed2 = build_article_embed(article(), ["3070"], {"extracted_price": "2. RTX 3070 7000"})
        channel = FakeChannel({1: FakeMessage(embed1), 2: FakeMessage(embed2)})
        provider = DiscordNotificationProvider(FakeBot(channel), self.storage)
        manager = DeliveryManager(
            storage=self.storage,
            crawler=DetailCrawler(
                {
                    "is_sold": False,
                    "raw_price_lines": ["1. RTX 3060 售出 5000", "2. RTX 3070 7000"],
                    "body_only": "1. RTX 3060 售出\n2. RTX 3070 7000",
                    "extracted_price": "1. RTX 3060 售出 5000 / 2. RTX 3070 7000",
                }
            ),
            auto_register_providers=False,
        )
        manager.register_provider("discord", provider)

        count = await manager.check_sold_deliveries_once(max_checks=1)
        self.assertEqual(count, 1)
        rows = {r["subscriber_id"]: r for r in self.storage.get_recent_unmarked_deliveries(limit=20)}
        self.assertNotIn(first["id"], rows)
        self.assertEqual(rows[second["id"]]["sold_marked"], 0)
        self.assertIsNotNone(channel.messages[1].edited_embed)
        self.assertIsNone(channel.messages[2].edited_embed)

    def test_update_existing_embed_preserves_fields_for_all_states(self):
        original = build_article_embed(
            article(title="[賣] RTX 3060"),
            ["3060"],
            {"extracted_price": "5000 元", "post_time": "2026-11-10"},
            scope_label="追蹤者",
        )
        before = {field.name: field.value for field in original.fields}
        footer = original.footer.text
        for state in ("SOLD", "RESERVED", "NO_MAIL", "DELETED"):
            updated = update_existing_embed_for_sold(
                original, {"sold_state": state, "deleted": state == "DELETED"}, "HardwareSale"
            )
            after = {field.name: field.value for field in updated.fields}
            self.assertEqual(after["命中關鍵字"], before["命中關鍵字"])
            self.assertEqual(after["欲售價格"], before["欲售價格"])
            self.assertEqual(updated.footer.text, footer)
            self.assertIn(
                {"SOLD": "[已售出]", "RESERVED": "[洽談中]", "NO_MAIL": "[暫勿來信]", "DELETED": "[原文已刪除]"}[state],
                updated.title,
            )

    async def test_discord_notfound_and_forbidden_mark_edit_failed(self):
        for index, error in enumerate(
            (
                discord.NotFound(MagicMock(status=404), "missing"),
                discord.Forbidden(MagicMock(status=403), "forbidden"),
            ),
            start=1,
        ):
            sub = self.add_subscriber(target_id=str(1100 + index))
            self.storage.record_delivery(sub["id"], article(), "456", "1", "3060")
            row = self.storage.get_recent_unmarked_deliveries(limit=20)[-1]
            channel = FakeChannel({1: FakeMessage(error=error)})
            provider = DiscordNotificationProvider(FakeBot(channel), self.storage)
            self.assertFalse(await provider.update_sold(sub, row, sold_detail()))
            self.assertEqual(
                self.storage._conn.execute(
                    "SELECT edit_failed FROM deliveries WHERE id = ?", (row["id"],)
                ).fetchone()[0],
                1,
            )
            self.storage._conn.execute("DELETE FROM deliveries")
            self.storage._conn.commit()

    async def test_telegram_400_and_403_mark_edit_failed(self):
        api = MagicMock()
        provider = TelegramNotificationProvider(api_client=api, storage=self.storage)
        for error_code in (400, 403):
            sub = self.add_subscriber(platform="telegram", target_id=str(error_code))
            self.storage.record_delivery(sub["id"], article(), "123", "7", "3060")
            row = self.storage.get_recent_unmarked_deliveries(limit=20)[-1]
            api.edit_message_text = AsyncMock(
                return_value={"ok": False, "error_code": error_code, "description": "bad message"}
            )
            self.assertFalse(await provider.update_sold(sub, row, sold_detail()))
            self.assertEqual(
                self.storage._conn.execute(
                    "SELECT edit_failed FROM deliveries WHERE id = ?", (row["id"],)
                ).fetchone()[0],
                1,
            )
            self.storage._conn.execute("DELETE FROM deliveries")
            self.storage._conn.commit()

    async def test_http_404_marks_card_deleted(self):
        sub = self.add_subscriber(target_id="deleted")
        self.storage.record_delivery(sub["id"], article(), "456", "1", "3060")
        provider = RecordingProvider()
        manager = DeliveryManager(
            storage=self.storage,
            crawler=DetailCrawler({"fetch_status": 404, "deleted": True}),
            auto_register_providers=False,
        )
        manager.register_provider("discord", provider)
        self.assertEqual(await manager.check_sold_deliveries_once(max_checks=1), 1)
        self.assertEqual(provider.calls[0][2]["sold_state"], "DELETED")
        self.assertTrue(provider.calls[0][2]["deleted"])
        self.assertEqual(self.storage.get_recent_unmarked_deliveries(limit=20), [])

    async def test_eight_subscribers_same_url_are_all_updated(self):
        subscribers = [self.add_subscriber(target_id=f"u{i}") for i in range(8)]
        for i, sub in enumerate(subscribers):
            self.storage.record_delivery(sub["id"], article(), "456", str(i + 1), "3060")
        self.assertEqual(len(self.storage.get_recent_unmarked_deliveries(limit=2)), 8)
        provider = RecordingProvider()
        manager = DeliveryManager(
            storage=self.storage,
            crawler=DetailCrawler(sold_detail()),
            auto_register_providers=False,
        )
        manager.register_provider("discord", provider)
        self.assertEqual(await manager.check_sold_deliveries_once(max_checks=1), 8)
        self.assertEqual(len(provider.calls), 8)

    async def test_email_delivery_is_marked_sold_without_edit_failure(self):
        sub = self.add_subscriber(platform="email", target_id="a@example.com")
        self.storage.record_delivery(sub["id"], article(), "email", "1", "3060")
        manager = DeliveryManager(
            storage=self.storage,
            crawler=DetailCrawler(sold_detail()),
            auto_register_providers=False,
        )
        self.assertEqual(await manager.check_sold_deliveries_once(max_checks=1), 0)
        row = self.storage._conn.execute(
            "SELECT sold_marked, edit_failed FROM deliveries"
        ).fetchone()
        self.assertEqual(tuple(row), (1, 0))

    def test_dedupe_allows_repost_after_prior_sale(self):
        sub = self.add_subscriber(target_id="dedupe")
        old = article(url=ARTICLE_URL, title="[賣] RTX 3060")
        new_url = "https://www.ptt.cc/bbs/HardwareSale/M.1234567891.A.DEF456.html"
        new = article(url=new_url, title="[賣] RTX 3060")
        self.storage.record_delivery(sub["id"], old, None, "1", "3060")
        dedupe = compute_dedupe_hash("HardwareSale", "seller", "[賣] RTX 3060")
        self.assertTrue(self.storage.is_duplicate_delivery(sub["id"], ARTICLE_URL, dedupe))
        self.storage.mark_delivery_sold(ARTICLE_URL)
        self.assertFalse(self.storage.is_duplicate_delivery(sub["id"], new_url, dedupe))
        self.assertEqual(self.storage.is_duplicate_delivery(sub["id"], ARTICLE_URL, dedupe), True)


class TestSchemaV5Migration(unittest.TestCase):
    def test_v4_to_v5_backup_and_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "legacy.db")
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO settings VALUES ('schema_version', '4');
                CREATE TABLE subscribers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
                    discord_id TEXT NOT NULL, display_name TEXT DEFAULT '',
                    push_channel_id TEXT, push_mode TEXT NOT NULL DEFAULT 'keyword',
                    tier TEXT NOT NULL DEFAULT 'free', enabled INTEGER NOT NULL DEFAULT 1,
                    dm_failed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    platform TEXT NOT NULL DEFAULT 'discord', target_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscriber_id INTEGER NOT NULL, article_url TEXT NOT NULL,
                    board TEXT NOT NULL DEFAULT 'HardwareSale', title TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT '', dedupe_hash TEXT NOT NULL,
                    channel_id TEXT, message_id TEXT, delivered_at INTEGER NOT NULL,
                    sold_marked INTEGER NOT NULL DEFAULT 0,
                    last_sold_check_at INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            conn.commit()
            conn.close()
            storage = BotStorage(path)
            self.assertTrue(os.path.exists(path + ".bak.v4"))
            self.assertEqual(storage.get_schema_user_version(), 5)
            self.assertEqual(storage.get_setting("schema_version"), "4")
            for _ in range(4):
                storage.backup_database()
            self.assertTrue(os.path.exists(path + ".bak.1"))
            self.assertTrue(os.path.exists(path + ".bak.2"))
            self.assertTrue(os.path.exists(path + ".bak.3"))
            storage.close()


if __name__ == "__main__":
    unittest.main()
