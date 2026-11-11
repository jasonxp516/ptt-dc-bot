"""
Regression tests for v1.3.0: delivery retry queue (F01), Telegram data deletion and block list
(R06), Email copy mode (R04), board recovery notice (F06), Groq model probe (T5) and
sold follow-up scaling (R13).
"""

import asyncio
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config

if not os.environ.get("LOG_PATH") or not os.environ.get("DB_PATH"):
    _TMPDIR = tempfile.TemporaryDirectory()
    if not os.environ.get("LOG_PATH"):
        config.LOG_PATH = os.path.join(_TMPDIR.name, "bot.log")
        os.environ["LOG_PATH"] = config.LOG_PATH
    if not os.environ.get("DB_PATH"):
        config.DB_PATH = os.path.join(_TMPDIR.name, "bot_data.db")
        os.environ["DB_PATH"] = config.DB_PATH

import storage as storage_module
from bot import PTTDiscordBot, cmd_email_copy
from delivery import BaseNotificationProvider, DeliveryDispatcher, DeliveryManager
from groq_scheduler import GroqBusyError, GroqScheduler
from storage import BotStorage
from telegram_bot import TelegramBot
from tests.test_review_fixes import (
    BOARD,
    FakeCrawler,
    FakeDelivery,
    make_article,
    make_poll_bot,
)


def make_item(art, kw="3060"):
    return {
        "article": art,
        "matched_keywords": [kw],
        "matched_keyword": kw,
        "detail": {"full_body": "x" * 5000, "is_sold": False, "extracted_price": "5000"},
        "fuzzy_matches": [],
    }


class FlakyProvider(BaseNotificationProvider):
    """Records a delivery only while `working` is True (like a real provider on success)."""

    platform_name = "discord"

    def __init__(self, storage):
        self.storage = storage
        self.working = True
        self.sent = []

    async def deliver(self, subscriber, items):
        if not self.working:
            return 0
        for it in items:
            self.storage.record_delivery(
                subscriber_id=subscriber["id"],
                article=it["article"],
                channel_id="1",
                message_id="2",
                matched_keyword=it.get("matched_keyword", ""),
            )
            self.sent.append((subscriber["id"], it["article"]["url"]))
        return len(items)

    async def update_sold(self, subscriber, delivery_record, detail):
        return False


class MailProvider(BaseNotificationProvider):
    platform_name = "email"

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.default_to_emails = ["owner@example.com"]

    async def deliver(self, subscriber, items, record=True):
        self.calls.append((subscriber, [it["article"]["url"] for it in items], record))
        if self.fail:
            raise RuntimeError("smtp down")
        return len(items)

    async def update_sold(self, subscriber, delivery_record, detail):
        return False


class _Case(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = BotStorage(os.path.join(self.tmp.name, "bot.db"), default_board=BOARD)
        self.sub_a = self.storage.add_subscriber(scope="user", discord_id="111", display_name="a")
        self.sub_b = self.storage.add_subscriber(scope="user", discord_id="222", display_name="b")
        self.provider = FlakyProvider(self.storage)
        self.dispatcher = DeliveryDispatcher(storage=self.storage)
        self.dispatcher.register_provider("discord", self.provider)
        self.now = int(time.time())

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def manager(self, crawler=None):
        return DeliveryManager(
            bot=None,
            storage=self.storage,
            crawler=crawler,
            dispatcher=self.dispatcher,
            auto_register_providers=False,
        )

    def make_due(self, sub_id):
        self.storage._conn.execute(
            "UPDATE pending_deliveries SET next_attempt_at = 0 WHERE subscriber_id = ?", (sub_id,)
        )
        self.storage._conn.commit()


# ---------------------------------------------------------------------------
# F01: delivery retry queue
# ---------------------------------------------------------------------------
class TestPendingQueue(_Case):
    async def test_failed_delivery_is_queued_then_resent_once(self):
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        self.provider.working = False
        self.assertEqual(await self.dispatcher.dispatch(self.sub_a, [make_item(art)]), 0)
        self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 1)
        # not due yet -> nothing is retried
        mgr = self.manager(FakeCrawler([]))
        self.provider.working = True
        self.assertEqual(await mgr.retry_pending_deliveries([self.sub_a]), 0)

        self.make_due(self.sub_a["id"])
        self.assertEqual(await mgr.retry_pending_deliveries([self.sub_a]), 1)
        self.assertEqual(self.provider.sent, [(self.sub_a["id"], art["url"])])
        self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 0)
        # a later cycle must not send it again
        self.assertEqual(await mgr.retry_pending_deliveries([self.sub_a]), 0)
        self.assertEqual(len(self.provider.sent), 1)

    async def test_successful_delivery_is_not_queued(self):
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        self.assertEqual(await self.dispatcher.dispatch(self.sub_a, [make_item(art)]), 1)
        self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 0)

    async def test_gives_up_after_all_retries(self):
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        item = make_item(art)
        self.provider.working = False
        mgr = self.manager(FakeCrawler([]))
        await self.dispatcher.dispatch(self.sub_a, [item])
        for _ in range(len(storage_module.PENDING_DELIVERY_BACKOFF_SEC) - 1):
            self.make_due(self.sub_a["id"])
            await mgr.retry_pending_deliveries([self.sub_a])
            self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 1)
        self.make_due(self.sub_a["id"])
        await mgr.retry_pending_deliveries([self.sub_a])
        self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 0)

    async def test_backoff_grows_between_attempts(self):
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        item = make_item(art)
        self.storage.note_pending_failure(self.sub_a["id"], item)
        first = self.storage._conn.execute("SELECT next_attempt_at FROM pending_deliveries").fetchone()[0]
        self.storage.note_pending_failure(self.sub_a["id"], item)
        second = self.storage._conn.execute("SELECT next_attempt_at FROM pending_deliveries").fetchone()[0]
        self.assertGreaterEqual(first - self.now, storage_module.PENDING_DELIVERY_BACKOFF_SEC[0])
        self.assertGreaterEqual(second - self.now, storage_module.PENDING_DELIVERY_BACKOFF_SEC[1])

    async def test_sold_or_deleted_article_is_dropped_not_resent(self):
        sold = make_article(self.now - 60, 1, "[賣] RTX 3060")
        gone = make_article(self.now - 50, 2, "[賣] RTX 3060 b")
        self.provider.working = False
        await self.dispatcher.dispatch(self.sub_a, [make_item(sold), make_item(gone)])
        self.provider.working = True
        self.make_due(self.sub_a["id"])
        crawler = FakeCrawler(
            [],
            detail_by_url={
                sold["url"]: {"is_sold": True, "sold_state": "SOLD"},
                gone["url"]: {"fetch_status": 404, "deleted": True, "full_body": ""},
            },
        )
        self.assertEqual(await self.manager(crawler).retry_pending_deliveries([self.sub_a]), 0)
        self.assertEqual(self.provider.sent, [])
        self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 0)

    async def test_queued_duplicate_leaves_the_queue(self):
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        self.storage.note_pending_failure(self.sub_a["id"], make_item(art))
        self.storage.record_delivery(self.sub_a["id"], art, "1", "2", "3060")  # delivered elsewhere
        self.make_due(self.sub_a["id"])
        self.assertEqual(await self.manager(FakeCrawler([])).retry_pending_deliveries([self.sub_a]), 0)
        self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 0)
        self.assertEqual(self.provider.sent, [])

    async def test_queue_is_isolated_per_subscriber(self):
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        self.provider.working = False
        await self.dispatcher.dispatch(self.sub_a, [make_item(art)])
        self.provider.working = True
        self.make_due(self.sub_a["id"])
        self.assertEqual(self.storage.get_due_pending_deliveries(self.sub_b["id"]), [])
        self.assertEqual(await self.manager(FakeCrawler([])).retry_pending_deliveries([self.sub_b]), 0)
        self.assertEqual(self.provider.sent, [])
        self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 1)

    async def test_paused_subscriber_is_not_retried(self):
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        self.provider.working = False
        await self.dispatcher.dispatch(self.sub_a, [make_item(art)])
        self.provider.working = True
        self.make_due(self.sub_a["id"])
        self.storage.set_subscriber_enabled(self.sub_a["id"], False)
        enabled = self.storage.get_all_enabled_subscribers()
        self.assertEqual(await self.manager(FakeCrawler([])).retry_pending_deliveries(enabled), 0)
        self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 1)

    async def test_forget_removes_queue_and_email_copy_setting(self):
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        self.provider.working = False
        await self.dispatcher.dispatch(self.sub_a, [make_item(art)])
        self.storage.set_setting(f"email_copy:{self.sub_a['id']}", "1")
        self.storage.forget_subscriber_by_id(self.sub_a["id"])
        rows = self.storage._conn.execute("SELECT COUNT(*) FROM pending_deliveries").fetchone()[0]
        self.assertEqual(rows, 0)
        self.assertIsNone(self.storage.get_setting(f"email_copy:{self.sub_a['id']}"))

    async def test_payload_body_is_trimmed_and_old_rows_are_purged(self):
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        self.storage.note_pending_failure(self.sub_a["id"], make_item(art))
        self.make_due(self.sub_a["id"])
        item = self.storage.get_due_pending_deliveries(self.sub_a["id"])[0]
        self.assertEqual(len(item["detail"]["full_body"]), 2000)
        self.storage._conn.execute(
            "UPDATE pending_deliveries SET created_at = ?",
            (self.now - storage_module.PENDING_DELIVERY_MAX_AGE_SEC - 10,),
        )
        self.storage._conn.commit()
        self.assertEqual(self.storage.get_due_pending_deliveries(self.sub_a["id"]), [])
        self.storage.cleanup_old_articles()
        self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 0)

    async def test_existing_database_gains_queue_table_without_version_change(self):
        self.assertEqual(self.storage.get_schema_user_version(), 5)
        self.assertEqual(self.storage.get_setting("schema_version"), "4")
        path = self.storage.db_path
        self.storage._conn.execute("DROP TABLE pending_deliveries")
        self.storage._conn.commit()
        self.storage.close()
        self.storage = BotStorage(path, default_board=BOARD)  # simulates opening a v1.2.1 file
        self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 0)
        self.assertIsNotNone(self.storage.get_subscriber_by_id(self.sub_a["id"]))

    async def test_poll_cycle_runs_retry_before_new_articles(self):
        delivery = FakeDelivery()
        delivery.retry_pending_deliveries = AsyncMock(return_value=2)
        self.storage.add_board(BOARD)
        self.storage.update_board_status(BOARD, True, "ok", scanned_now=True)
        bot = make_poll_bot(self.storage, FakeCrawler([]), delivery=delivery)
        summary = await PTTDiscordBot.run_single_poll_cycle(bot)
        delivery.retry_pending_deliveries.assert_awaited_once()
        self.assertEqual(summary["pushed"], 2)


# ---------------------------------------------------------------------------
# R04: Email copy mode
# ---------------------------------------------------------------------------
class TestEmailCopy(_Case):
    async def test_copy_is_sent_only_for_delivered_items_when_enabled(self):
        mail = MailProvider()
        self.dispatcher.register_provider("email", mail)
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        await self.dispatcher.dispatch(self.sub_a, [make_item(art)])
        self.assertEqual(mail.calls, [])  # not enabled

        self.storage.set_setting(f"email_copy:{self.sub_a['id']}", "1")
        art2 = make_article(self.now - 50, 2, "[賣] RTX 3060 b")
        await self.dispatcher.dispatch(self.sub_a, [make_item(art2)])
        self.assertEqual(len(mail.calls), 1)
        self.assertEqual(mail.calls[0][1], [art2["url"]])
        self.assertFalse(mail.calls[0][2])  # record=False: no duplicate delivery row

        self.provider.working = False
        art3 = make_article(self.now - 40, 3, "[賣] RTX 3060 c")
        await self.dispatcher.dispatch(self.sub_a, [make_item(art3)])
        self.assertEqual(len(mail.calls), 1)  # main platform failed -> no mail

    async def test_copy_always_goes_to_configured_recipients(self):
        mail = MailProvider()
        self.dispatcher.register_provider("email", mail)
        self.storage.set_setting(f"email_copy:{self.sub_a['id']}", "1")
        sub = dict(self.sub_a, display_name="stranger@example.org", target_id="other@example.org")
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        await self.dispatcher.dispatch(sub, [make_item(art)])
        self.assertEqual(mail.calls[0][0]["target_id"], "owner@example.com")

        mail.default_to_emails = []
        art2 = make_article(self.now - 50, 2, "[賣] RTX 3060 b")
        await self.dispatcher.dispatch(sub, [make_item(art2)])
        self.assertEqual(len(mail.calls), 1)  # no EMAIL_TO -> nothing is mailed

    async def test_copy_is_scoped_to_the_enabled_subscriber(self):
        mail = MailProvider()
        self.dispatcher.register_provider("email", mail)
        self.storage.set_setting(f"email_copy:{self.sub_a['id']}", "1")
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        await self.dispatcher.dispatch(self.sub_b, [make_item(art)])
        self.assertEqual(mail.calls, [])

    async def test_mail_failure_does_not_affect_main_delivery(self):
        self.dispatcher.register_provider("email", MailProvider(fail=True))
        self.storage.set_setting(f"email_copy:{self.sub_a['id']}", "1")
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        self.assertEqual(await self.dispatcher.dispatch(self.sub_a, [make_item(art)]), 1)
        self.assertTrue(self.storage.has_delivery_record(self.sub_a["id"], art["url"]))
        self.assertEqual(self.storage.count_pending_deliveries(self.sub_a["id"]), 0)

    async def test_email_provider_record_false_skips_delivery_rows(self):
        from email_notifier import EmailNotificationProvider

        prov = EmailNotificationProvider(
            host="h", port=587, user="u", password="p", default_to="x@example.com",
            storage=self.storage,
        )
        prov._sync_send = MagicMock(return_value=True)
        art = make_article(self.now - 60, 1, "[賣] RTX 3060")
        with patch("email_notifier.asyncio.sleep", AsyncMock()):
            sent = await prov.deliver(dict(self.sub_a, target_id=""), [make_item(art)], record=False)
        self.assertEqual(sent, 1)
        self.assertFalse(self.storage.has_delivery_record(self.sub_a["id"], art["url"]))

    async def test_email_copy_command_is_owner_only(self):
        inter = MagicMock()
        inter.user.id = 999
        inter.response.send_message = AsyncMock()
        with patch("bot.is_bot_owner", return_value=False), patch("bot.bot") as fake_bot:
            await cmd_email_copy.callback(inter, "on", "auto")
            fake_bot.storage.set_setting.assert_not_called()
        self.assertIn("權限不足", inter.response.send_message.await_args.args[0])


# ---------------------------------------------------------------------------
# R06: Telegram data deletion, keyword removal and block list
# ---------------------------------------------------------------------------
class TestTelegramPrivacy(_Case):
    def make_tg(self, blocked=None, member_status="member"):
        api = MagicMock()
        api.send_message = AsyncMock(return_value={"ok": True})
        api.answer_callback_query = AsyncMock(return_value={"ok": True})
        api.get_chat_member = AsyncMock(return_value={"ok": True, "result": {"status": member_status}})
        tg = TelegramBot(
            token="1:x", storage=self.storage, api_client=api,
            admin_user_ids=[], blocked_user_ids=blocked or [],
        )
        return tg, api

    def private_msg(self, text, uid=42):
        return {"text": text, "chat": {"id": uid, "type": "private"}, "from": {"id": uid, "first_name": "u"}}

    async def test_private_forget_me_requires_confirmation_then_deletes(self):
        tg, api = self.make_tg()
        await tg._handle_message(self.private_msg("/keyword_add 3060"))
        sub = self.storage.get_subscriber("user", "telegram_42", platform="telegram", target_id="42")
        self.assertEqual(len(self.storage.get_keywords(subscriber_id=sub["id"])), 1)

        await tg._handle_message(self.private_msg("/forget_me"))
        self.assertIsNotNone(self.storage.get_subscriber_by_id(sub["id"]))  # only asked so far
        markup = api.send_message.await_args.kwargs["reply_markup"]
        yes_data = markup["inline_keyboard"][0][0]["callback_data"]
        self.assertEqual(yes_data, f"forget_yes:{sub['id']}")

        await tg._handle_callback_query(
            {"id": "c1", "data": yes_data, "from": {"id": 42},
             "message": {"chat": {"id": 42, "type": "private"}}}
        )
        self.assertIsNone(self.storage.get_subscriber_by_id(sub["id"]))
        self.assertEqual(self.storage.get_keywords(subscriber_id=sub["id"]), [])

    async def test_forget_button_from_another_chat_is_rejected(self):
        tg, api = self.make_tg()
        await tg._handle_message(self.private_msg("/start", uid=42))
        await tg._handle_message(self.private_msg("/start", uid=43))
        victim = self.storage.get_subscriber("user", "telegram_42", platform="telegram", target_id="42")
        await tg._handle_callback_query(
            {"id": "c1", "data": f"forget_yes:{victim['id']}", "from": {"id": 43},
             "message": {"chat": {"id": 43, "type": "private"}}}
        )
        self.assertIsNotNone(self.storage.get_subscriber_by_id(victim["id"]))

    async def test_group_member_cannot_forget_group_data(self):
        tg, api = self.make_tg(member_status="member")
        chat = {"id": -100, "type": "group", "title": "g"}
        sub = tg._resolve_subscriber(chat, {"id": 7})
        await tg._handle_callback_query(
            {"id": "c1", "data": f"forget_yes:{sub['id']}", "from": {"id": 7}, "message": {"chat": chat}}
        )
        self.assertIsNotNone(self.storage.get_subscriber_by_id(sub["id"]))
        await tg._handle_message({"text": "/forget_me", "chat": chat, "from": {"id": 7}})
        self.assertIn("權限不足", api.send_message.await_args.args[1])

    async def test_keyword_remove_by_id(self):
        tg, api = self.make_tg()
        await tg._handle_message(self.private_msg("/keyword_add 3060"))
        sub = self.storage.get_subscriber("user", "telegram_42", platform="telegram", target_id="42")
        kw_id = self.storage.get_keywords(subscriber_id=sub["id"])[0]["id"]
        await tg._handle_message(self.private_msg(f"/keyword_remove #{kw_id}"))
        self.assertEqual(self.storage.get_keywords(subscriber_id=sub["id"]), [])

    async def test_blocked_user_gets_no_response_and_no_rows(self):
        tg, api = self.make_tg(blocked=["42"])
        before = len(self.storage.list_subscribers())
        await tg._handle_message(self.private_msg("/keyword_add 3060"))
        await tg._handle_callback_query(
            {"id": "c1", "data": "forget_no", "from": {"id": 42},
             "message": {"chat": {"id": 42, "type": "private"}}}
        )
        api.send_message.assert_not_awaited()
        api.answer_callback_query.assert_not_awaited()
        self.assertEqual(len(self.storage.list_subscribers()), before)

    async def test_database_blacklist_applies_to_telegram(self):
        tg, api = self.make_tg()
        self.storage.set_blacklist("telegram_42")
        await tg._handle_message(self.private_msg("/start"))
        api.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# F06: board recovery notice
# ---------------------------------------------------------------------------
class TestBoardRecovery(_Case):
    async def test_recovery_notice_sent_once_after_alert(self):
        self.storage.add_board(BOARD)
        self.storage.update_board_status(BOARD, True, "ok", scanned_now=True)
        self.storage.add_keyword("3060", board=BOARD, subscriber_id=self.sub_a["id"])
        crawler = FakeCrawler([])
        bot = make_poll_bot(self.storage, crawler)
        bot._send_owner_notice = AsyncMock()

        async def failing(board, **kwargs):
            return False, [], "HTTP 503"

        ok_fetch = crawler.fetch_board_articles
        crawler.fetch_board_articles = failing
        for _ in range(5):
            await PTTDiscordBot.run_single_poll_cycle(bot)
        bot._send_board_failure_alert.assert_awaited()
        bot._send_owner_notice.assert_not_awaited()

        crawler.fetch_board_articles = ok_fetch
        await PTTDiscordBot.run_single_poll_cycle(bot)
        self.assertEqual(bot._send_owner_notice.await_count, 1)
        self.assertIn("恢復", bot._send_owner_notice.await_args.args[0])
        await PTTDiscordBot.run_single_poll_cycle(bot)
        self.assertEqual(bot._send_owner_notice.await_count, 1)

    async def test_no_recovery_notice_without_prior_alert(self):
        self.storage.add_board(BOARD)
        self.storage.update_board_status(BOARD, True, "ok", scanned_now=True)
        self.storage.add_keyword("3060", board=BOARD, subscriber_id=self.sub_a["id"])
        bot = make_poll_bot(self.storage, FakeCrawler([]))
        bot._send_owner_notice = AsyncMock()
        await PTTDiscordBot.run_single_poll_cycle(bot)
        bot._send_owner_notice.assert_not_awaited()


# ---------------------------------------------------------------------------
# T5: Groq model probe
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestModelProbe(unittest.IsolatedAsyncioTestCase):
    def sched(self, resp=None, exc=None):
        s = GroqScheduler(api_key="k", models=["m/a", "m/b"])
        session = MagicMock()
        session.get = MagicMock(side_effect=exc) if exc else MagicMock(return_value=resp)
        s._get_session = AsyncMock(return_value=session)
        return s

    async def test_missing_model_is_disabled_and_others_still_usable(self):
        s = self.sched(_Resp(200, {"data": [{"id": "m/a"}, {"id": "other"}]}))
        self.assertEqual(await s.probe_models(), ["m/b"])
        self.assertTrue(s.buckets["m/b"].disabled)
        self.assertFalse(s.buckets["m/a"].disabled)
        now = time.time()
        self.assertFalse(s.buckets["m/b"].is_available_for(now, 100, 1, allow_shared=True)[0])
        self.assertTrue(s.buckets["m/a"].is_available_for(now, 100, 1)[0])
        self.assertTrue(s.get_queue_status()["models"]["b"]["disabled"])

    async def test_probe_errors_disable_nothing(self):
        for s in (
            self.sched(_Resp(500, {})),
            self.sched(_Resp(200, {"data": []})),
            self.sched(exc=RuntimeError("network")),
        ):
            self.assertEqual(await s.probe_models(), [])
            self.assertFalse(any(b.disabled for b in s.buckets.values()))

    async def test_all_models_disabled_fails_immediately(self):
        s = GroqScheduler(api_key="k", models=["m/a"])
        s.buckets["m/a"].disabled = True
        t0 = time.time()
        with self.assertRaises(GroqBusyError):
            await s.submit_chat([{"role": "user", "content": "x"}])
        self.assertLess(time.time() - t0, 2.0)
        await s.close()

    async def test_owner_is_told_about_missing_models(self):
        bot = object.__new__(PTTDiscordBot)
        bot.scheduler = MagicMock()
        bot.scheduler.probe_models = AsyncMock(return_value=["m/b"])
        bot._send_owner_notice = AsyncMock()
        await PTTDiscordBot._probe_groq_models(bot)
        self.assertIn("m/b", bot._send_owner_notice.await_args.args[0])
        bot.scheduler.probe_models = AsyncMock(return_value=[])
        bot._send_owner_notice.reset_mock()
        await PTTDiscordBot._probe_groq_models(bot)
        bot._send_owner_notice.assert_not_awaited()


# ---------------------------------------------------------------------------
# R13: sold follow-up budget scales with the backlog
# ---------------------------------------------------------------------------
class TestSoldCheckScaling(_Case):
    async def test_budget_grows_with_open_followups(self):
        for i in range(100):
            art = make_article(self.now - 600 + i, i, f"[賣] item {i}")
            self.storage.record_delivery(self.sub_a["id"], art, "1", str(1000 + i), "item")
        self.assertEqual(self.storage.count_open_followup_urls(), 100)
        crawler = FakeCrawler([])
        fetched = []
        orig = crawler.fetch_article_full_detail

        async def counting(url, **kwargs):
            fetched.append(url)
            return await orig(url, **kwargs)

        crawler.fetch_article_full_detail = counting
        await self.manager(crawler).check_sold_deliveries_once(max_checks=2)
        self.assertEqual(len(fetched), 5)

    async def test_small_backlog_keeps_default_budget(self):
        for i in range(10):
            art = make_article(self.now - 600 + i, i, f"[賣] item {i}")
            self.storage.record_delivery(self.sub_a["id"], art, "1", str(1000 + i), "item")
        crawler = FakeCrawler([])
        fetched = []
        orig = crawler.fetch_article_full_detail

        async def counting(url, **kwargs):
            fetched.append(url)
            return await orig(url, **kwargs)

        crawler.fetch_article_full_detail = counting
        await self.manager(crawler).check_sold_deliveries_once(max_checks=2)
        self.assertEqual(len(fetched), 2)


if __name__ == "__main__":
    unittest.main()
