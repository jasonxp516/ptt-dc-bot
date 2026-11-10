"""
Regression tests for the 2026-11 review fixes.

Each test is tagged with the finding ID it covers (F01..F17, N1..N9).
Written as unittest.TestCase so both `pytest` and `unittest discover`
(used by the release gate) collect them.
"""

import asyncio
import os
import sqlite3
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

import discord

from bot import PTTDiscordBot, cmd_admin
from delivery import DeliveryManager, DiscordNotificationProvider
from embeds import build_article_embed
from groq_agent import GroqAgent
from groq_scheduler import (
    P0_INTERACTIVE,
    GroqBusyError,
    GroqScheduler,
    ScheduledGroqJob,
)
from ptt_crawler import PTTCrawler, apply_keyword_view, article_post_ts
from storage import BotStorage, price_for_budget_check
from telegram_bot import (
    TelegramAPIClient,
    TelegramBot,
    TelegramNotificationProvider,
    format_article_card,
    parse_keyword_add_args,
)

BOARD = "HardwareSale"


def make_url(ts: int, idx: int, board: str = BOARD) -> str:
    return f"https://www.ptt.cc/bbs/{board}/M.{ts}.A.{idx:03X}.html"


def make_article(ts: int, idx: int, title: str, board: str = BOARD) -> dict:
    return {
        "board": board,
        "title": title,
        "url": make_url(ts, idx, board),
        "author": f"seller{idx}",
        "date": "11/10",
        "nrec": "0",
        "category": "賣",
    }


class FakeCrawler:
    """Minimal crawler double for run_single_poll_cycle."""

    def __init__(self, articles, detail_by_url=None):
        self.articles = list(articles)
        self.detail_by_url = detail_by_url or {}
        self.fetch_calls = []

    def is_in_cooldown(self):
        return False, 0

    async def fetch_board_articles(self, board, fetch_prev_if_few=True, max_pages=1, stop_when=None):
        self.fetch_calls.append({"max_pages": max_pages, "stop_when": stop_when})
        return True, list(self.articles), "ok"

    async def fetch_article_full_detail(self, url, **kwargs):
        d = self.detail_by_url.get(url)
        if d is None:
            d = {"full_body": "body", "raw_price_lines": [], "is_sold": False}
        d = dict(d)
        d.setdefault("fetch_status", 200)
        d.setdefault("full_body", "body")
        return d


class FakeAgent:
    def __init__(self, fuzzy_complete=True, fuzzy_hits=None):
        self.fuzzy_complete = fuzzy_complete
        self.fuzzy_hits = fuzzy_hits or []

    async def verify_unsold_semantic(self, art, detail, kw, priority=None):
        return {"is_available": not detail.get("is_sold", False)}

    async def evaluate_article_fuzzy_rules_ex(self, art, detail, rules):
        return list(self.fuzzy_hits), self.fuzzy_complete


class FakeDelivery:
    def __init__(self):
        self.sent = []

    async def deliver_subscriber_matches(self, sub, items):
        self.sent.append((sub["id"], items))
        return len(items)

    async def check_sold_deliveries_once(self, max_checks=2):
        return 0


def make_poll_bot(storage, crawler, agent=None, delivery=None):
    b = object.__new__(PTTDiscordBot)
    b.storage = storage
    b.crawler = crawler
    b.agent = agent or FakeAgent()
    b.delivery = delivery or FakeDelivery()
    b._poll_lock = asyncio.Lock()
    b._write_heartbeat = lambda: None
    b._send_board_failure_alert = AsyncMock()
    return b


class _StorageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = BotStorage(os.path.join(self.tmp.name, "bot.db"), default_board=BOARD)
        self.storage.add_board(BOARD)
        self.storage.update_board_status(BOARD, True, "ok", scanned_now=True)

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def add_sub(self, uid: str, enabled: bool = True):
        return self.storage.add_subscriber(
            scope="user", discord_id=uid, display_name=uid, enabled=enabled
        )


# ---------------------------------------------------------------------------
# F16: item token / price selection
# ---------------------------------------------------------------------------
class TestF16KeywordViewPrice(_StorageCase):
    BASE = {
        "raw_price_lines": ["1. GTX 1060 $1000", "2. RTX 3060 $6000"],
        "full_body": "1. GTX 1060 $1000\n2. RTX 3060 $6000\n",
    }

    def test_primary_token_independent_of_order(self):
        sub = self.add_sub("u1")
        self.storage.add_keyword("3060 台北", board=BOARD, subscriber_id=sub["id"])
        hits = self.storage.match_article_keywords_detailed(BOARD, "[賣/台北] RTX 3060 GTX 1060")
        self.assertEqual(hits[0]["primary_token"], "3060")

    def test_view_picks_item_price_for_both_orders(self):
        for kw in ("3060 台北", "台北 3060"):
            view = apply_keyword_view(self.BASE, kw)
            self.assertEqual(price_for_budget_check(view), 6000, kw)
            self.assertFalse(view.get("price_ambiguous"), kw)

    def test_ambiguous_multi_item_price_uses_lowest_and_is_flagged(self):
        view = apply_keyword_view(self.BASE, "4090")
        self.assertTrue(view.get("price_ambiguous"))
        self.assertEqual(price_for_budget_check(view), 1000)

    def test_poll_cycle_budget_filter_is_order_independent(self):
        now = int(time.time())
        s1 = self.add_sub("u1")
        s2 = self.add_sub("u2")
        self.storage.add_keyword("3060 台北", board=BOARD, subscriber_id=s1["id"], max_price=2000)
        self.storage.add_keyword("台北 3060", board=BOARD, subscriber_id=s2["id"], max_price=2000)
        art = make_article(now - 60, 1, "[賣/台北] GTX 1060 / RTX 3060")
        crawler = FakeCrawler([art], {art["url"]: dict(self.BASE)})
        delivery = FakeDelivery()
        bot = make_poll_bot(self.storage, crawler, delivery=delivery)
        asyncio.run(PTTDiscordBot.run_single_poll_cycle(bot))
        self.assertEqual(delivery.sent, [], "3060 at 6000 must not pass a 2000 budget")


# ---------------------------------------------------------------------------
# N1 / F02: stale articles, flood cap, backfill paging
# ---------------------------------------------------------------------------
class TestN1F02PollWindow(_StorageCase):
    def test_article_post_ts(self):
        self.assertEqual(article_post_ts(make_url(1792681775, 1)), 1792681775)
        self.assertIsNone(article_post_ts("https://www.ptt.cc/bbs/X/index.html"))

    def test_stale_unseen_articles_are_marked_seen_not_pushed(self):
        now = int(time.time())
        sub = self.add_sub("u1")
        self.storage.add_keyword("3060", board=BOARD, subscriber_id=sub["id"])
        old = make_article(now - 20 * 86400, 1, "[賣] RTX 3060 old")
        fresh = make_article(now - 60, 2, "[賣] RTX 3060 fresh")
        delivery = FakeDelivery()
        bot = make_poll_bot(self.storage, FakeCrawler([old, fresh]), delivery=delivery)
        asyncio.run(PTTDiscordBot.run_single_poll_cycle(bot))
        pushed_urls = [it["article"]["url"] for _, items in delivery.sent for it in items]
        self.assertEqual(pushed_urls, [fresh["url"]])
        self.assertEqual(self.storage.filter_unseen_articles([old, fresh]), [])

    def test_flood_cap_processes_oldest_and_keeps_rest_unseen(self):
        now = int(time.time())
        sub = self.add_sub("u1")
        self.storage.add_keyword("3060", board=BOARD, subscriber_id=sub["id"])
        arts = [make_article(now - 3000 + i, i, f"[賣] RTX 3060 #{i}") for i in range(25)]
        delivery = FakeDelivery()
        bot = make_poll_bot(self.storage, FakeCrawler(arts), delivery=delivery)
        asyncio.run(PTTDiscordBot.run_single_poll_cycle(bot))
        unseen = self.storage.filter_unseen_articles(arts)
        self.assertEqual([a["url"] for a in unseen], [a["url"] for a in arts[20:]])
        # next cycle picks up the remaining 5
        asyncio.run(PTTDiscordBot.run_single_poll_cycle(bot))
        self.assertEqual(self.storage.filter_unseen_articles(arts), [])
        pushed = {it["article"]["url"] for _, items in delivery.sent for it in items}
        self.assertEqual(pushed, {a["url"] for a in arts})

    def test_backfill_stop_predicate(self):
        now = int(time.time())
        bot = make_poll_bot(self.storage, FakeCrawler([]))
        a = make_article(now - 60, 1, "a")
        self.assertFalse(PTTDiscordBot._backfill_reached_known_point(bot, [a]))
        self.storage.mark_articles_seen([a])
        self.assertTrue(PTTDiscordBot._backfill_reached_known_point(bot, [a]))
        stale = make_article(now - 3 * 86400, 2, "old")
        self.assertTrue(PTTDiscordBot._backfill_reached_known_point(bot, [stale]))

    def test_initialized_board_requests_backfill_pages(self):
        crawler = FakeCrawler([])
        self.add_sub("u1")
        self.storage.add_keyword("3060", board=BOARD, subscriber_id=1)
        bot = make_poll_bot(self.storage, crawler)
        asyncio.run(PTTDiscordBot.run_single_poll_cycle(bot))
        self.assertGreater(crawler.fetch_calls[0]["max_pages"], 1)
        self.assertIsNotNone(crawler.fetch_calls[0]["stop_when"])


# ---------------------------------------------------------------------------
# F03: fuzzy AI failure keeps the article for retry
# ---------------------------------------------------------------------------
class TestF03FuzzyRetry(_StorageCase):
    def test_incomplete_fuzzy_evaluation_keeps_article_unseen(self):
        now = int(time.time())
        sub = self.add_sub("u1")
        self.storage.add_fuzzy_rule("便宜的顯卡", board=BOARD, subscriber_id=sub["id"])
        art = make_article(now - 60, 1, "[賣] 顯卡")
        bot = make_poll_bot(self.storage, FakeCrawler([art]), agent=FakeAgent(fuzzy_complete=False))
        asyncio.run(PTTDiscordBot.run_single_poll_cycle(bot))
        self.assertEqual(len(self.storage.filter_unseen_articles([art])), 1)

        bot.agent = FakeAgent(fuzzy_complete=True)
        asyncio.run(PTTDiscordBot.run_single_poll_cycle(bot))
        self.assertEqual(self.storage.filter_unseen_articles([art]), [])


# ---------------------------------------------------------------------------
# F13: re-adding a tracked board must not rebuild the global baseline
# ---------------------------------------------------------------------------
class TestF13NoRebaseline(_StorageCase):
    def test_verify_and_add_initialized_board_skips_baseline(self):
        crawler = MagicMock()
        crawler.fetch_board_articles = AsyncMock(return_value=(True, [], "ok"))
        crawler.verify_board = AsyncMock(return_value=(True, BOARD, "ok"))
        agent = GroqAgent(api_key="", storage=self.storage, crawler=crawler)
        self.storage.mark_articles_seen = MagicMock(wraps=self.storage.mark_articles_seen)
        log, out, _ = asyncio.run(
            agent._execute_tool("verify_and_add_board", {"board_name": BOARD}, subscriber_id=1)
        )
        crawler.fetch_board_articles.assert_not_called()
        self.storage.mark_articles_seen.assert_not_called()
        self.assertIn("already_tracked", out)


# ---------------------------------------------------------------------------
# F11: paused rules keep their board registration
# ---------------------------------------------------------------------------
class TestF11BoardLifecycle(_StorageCase):
    def test_prune_keeps_board_of_paused_subscriber(self):
        sub = self.add_sub("u1")
        self.storage.add_keyword("macbook", board="MacShop", subscriber_id=sub["id"])
        self.storage.set_subscriber_enabled(sub["id"], False)
        self.storage.prune_idle_boards(min_idle_seconds=0)
        self.assertTrue(self.storage.is_board_tracked("MacShop"))

    def test_resume_restores_missing_board(self):
        sub = self.add_sub("u1")
        self.storage.add_keyword("macbook", board="MacShop", subscriber_id=sub["id"])
        self.storage.set_subscriber_enabled(sub["id"], False)
        self.storage.remove_board("MacShop")  # simulate loss under an older version
        self.storage.set_subscriber_enabled(sub["id"], True)
        self.assertTrue(self.storage.is_board_tracked("MacShop"))

    def test_any_rules_includes_paused(self):
        a = self.add_sub("a")
        self.storage.add_keyword("macbook", board="MacShop", subscriber_id=a["id"])
        self.storage.set_subscriber_enabled(a["id"], False)
        self.assertFalse(self.storage.has_active_rules_for_board("MacShop"))
        self.assertTrue(self.storage.has_any_rules_for_board("MacShop"))


# ---------------------------------------------------------------------------
# F04: backup success semantics
# ---------------------------------------------------------------------------
class TestF04Backup(_StorageCase):
    def test_failed_backup_does_not_mark_day_done(self):
        bdir = os.path.join(self.tmp.name, "backups")
        with patch.object(self.storage, "backup_database_atomic", side_effect=OSError("disk full")):
            self.assertIsNone(self.storage.run_daily_maintenance(backup_dir=bdir))
        self.assertEqual(self.storage.get_setting("last_maintenance_day", ""), "")
        # retry is throttled, then succeeds once the throttle expires
        self.storage._next_backup_retry_ts = 0.0
        path = self.storage.run_daily_maintenance(backup_dir=bdir)
        self.assertTrue(path and os.path.exists(path))
        self.assertNotEqual(self.storage.get_setting("last_maintenance_day", ""), "")
        con = sqlite3.connect(path)
        try:
            self.assertEqual(con.execute("PRAGMA quick_check").fetchone()[0], "ok")
            con.execute("SELECT COUNT(*) FROM subscribers").fetchone()
        finally:
            con.close()


# ---------------------------------------------------------------------------
# F12 / F15: sold follow-up uses the original channel, temporary states stay open
# ---------------------------------------------------------------------------
class _Msg:
    def __init__(self, embed):
        self.embeds = [embed]
        self.edits = []

    async def edit(self, *, embed):
        self.edits.append(embed)
        self.embeds = [embed]


class _Chan:
    def __init__(self, cid, msgs):
        self.id = cid
        self.msgs = msgs
        self.guild = None

    async def fetch_message(self, mid):
        return self.msgs[int(mid)]


class _Bot:
    def __init__(self, channels):
        self.channels = {c.id: c for c in channels}

    def get_channel(self, cid):
        return self.channels.get(cid)

    async def fetch_channel(self, cid):
        raise discord.NotFound(MagicMock(status=404), "gone")


class _SeqCrawler:
    def __init__(self, details):
        self.details = list(details)

    def is_in_cooldown(self):
        return False, 0

    async def fetch_article_full_detail(self, *a, **k):
        return dict(self.details.pop(0))


class TestF12F15SoldFollowup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = BotStorage(os.path.join(self.tmp.name, "bot.db"))
        self.now = int(time.time())
        self.art = make_article(self.now - 60, 1, "[賣] RTX 3060")

    async def asyncTearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def _setup(self, details, push_channel=None):
        sub = self.storage.add_subscriber(scope="user", discord_id="111", display_name="u")
        self.storage.record_delivery(sub["id"], self.art, "500", "1", "3060")
        chan_a = _Chan(500, {1: _Msg(build_article_embed(self.art, ["3060"], {}))})
        chan_b = _Chan(600, {})
        if push_channel:
            self.storage.update_subscriber_channel(sub["id"], push_channel)
        provider = DiscordNotificationProvider(_Bot([chan_a, chan_b]), self.storage)
        mgr = DeliveryManager(storage=self.storage, crawler=_SeqCrawler(details),
                              auto_register_providers=False)
        mgr.register_provider("discord", provider)
        return sub, chan_a, mgr

    def _row(self):
        return self.storage._conn.execute(
            "SELECT sold_marked, sold_state FROM deliveries"
        ).fetchone()

    async def test_reserved_then_sold(self):
        reserved = {"is_sold": True, "sold_state": "RESERVED", "fetch_status": 200}
        sold = {"is_sold": True, "sold_state": "SOLD", "fetch_status": 200}
        _, chan, mgr = self._setup([reserved, sold])
        await mgr.check_sold_deliveries_once(max_checks=1)
        self.assertEqual(tuple(self._row()), (0, "RESERVED"))
        self.storage._conn.execute("UPDATE deliveries SET last_sold_check_at = 0")
        await mgr.check_sold_deliveries_once(max_checks=1)
        self.assertEqual(tuple(self._row()), (1, "SOLD"))
        self.assertIn("[已售出]", chan.msgs[1].embeds[0].title)

    async def test_reserved_then_available_restores_card(self):
        reserved = {"is_sold": True, "sold_state": "RESERVED", "fetch_status": 200}
        avail = {"is_sold": False, "fetch_status": 200, "full_body": "RTX 3060 $5000"}
        _, chan, mgr = self._setup([reserved, avail])
        await mgr.check_sold_deliveries_once(max_checks=1)
        self.assertIn("[洽談中]", chan.msgs[1].embeds[0].title)
        await mgr.check_sold_deliveries_once(max_checks=1)
        self.assertEqual(tuple(self._row()), (0, "AVAILABLE"))
        self.assertNotIn("[洽談中]", chan.msgs[1].embeds[0].title)

    async def test_old_card_updated_in_original_channel_after_push_change(self):
        sold = {"is_sold": True, "sold_state": "SOLD", "fetch_status": 200}
        _, chan_a, mgr = self._setup([sold], push_channel="600")
        n = await mgr.check_sold_deliveries_once(max_checks=1)
        self.assertEqual(n, 1)
        self.assertEqual(len(chan_a.msgs[1].edits), 1)


# ---------------------------------------------------------------------------
# F08: priority gate cancellation
# ---------------------------------------------------------------------------
class TestF08GateCancel(unittest.IsolatedAsyncioTestCase):
    async def _assert_acquirable(self, c):
        t = asyncio.create_task(c._acquire_priority_slot(1))
        await asyncio.wait_for(t, 1.0)
        c._release_priority_slot()

    async def test_cancel_waiting_then_release(self):
        c = PTTCrawler()
        await c._acquire_priority_slot(1)
        waiters = [asyncio.create_task(c._acquire_priority_slot(1)) for _ in range(3)]
        await asyncio.sleep(0)
        waiters[1].cancel()
        await asyncio.gather(waiters[1], return_exceptions=True)
        c._release_priority_slot()  # -> waiter 0
        await asyncio.wait_for(waiters[0], 1.0)
        c._release_priority_slot()  # -> waiter 2 (1 was cancelled)
        await asyncio.wait_for(waiters[2], 1.0)
        c._release_priority_slot()
        await self._assert_acquirable(c)

    async def test_cancel_during_handoff(self):
        c = PTTCrawler()
        await c._acquire_priority_slot(1)
        w = asyncio.create_task(c._acquire_priority_slot(1))
        await asyncio.sleep(0)
        c._release_priority_slot()  # hand-off sets the event ...
        w.cancel()  # ... but the waiter is cancelled before it resumes
        await asyncio.gather(w, return_exceptions=True)
        await self._assert_acquirable(c)


# ---------------------------------------------------------------------------
# F07: AI deadline set at submit time
# ---------------------------------------------------------------------------
class TestF07Deadline(unittest.IsolatedAsyncioTestCase):
    async def test_expired_job_is_rejected_before_any_request(self):
        sched = GroqScheduler(api_key="k")
        fut = asyncio.get_running_loop().create_future()
        job = ScheduledGroqJob(
            priority=P0_INTERACTIVE, seq=1,
            payload={"messages": [], "tools": None, "temperature": 0, "max_tokens": 10, "json_mode": False},
            allowed_models=list(sched.buckets.keys()), est_tokens=10, future=fut,
            deadline=time.time() - 1,
        )
        with patch.object(sched, "_get_session", AsyncMock()) as gs:
            with self.assertRaises(GroqBusyError):
                await sched._execute_job(job)
            gs.return_value.post.assert_not_called()
        await sched.close()

    async def test_submit_sets_deadline(self):
        sched = GroqScheduler(api_key="k")
        captured = {}

        async def fake_put(job):
            captured["job"] = job
            job.future.set_result({"ok": True})

        sched._ensure_workers = lambda: None
        sched._queue.put = fake_put
        before = time.time()
        await sched.submit_chat([{"role": "user", "content": "x"}], priority=P0_INTERACTIVE)
        self.assertAlmostEqual(captured["job"].deadline - before, 30.0, delta=1.0)


# ---------------------------------------------------------------------------
# F09: /admin restricted to the bot owner
# ---------------------------------------------------------------------------
class TestF09AdminOwnerOnly(unittest.IsolatedAsyncioTestCase):
    async def test_home_guild_channel_manager_is_denied(self):
        inter = MagicMock()
        inter.user.id = 999
        inter.guild.id = int(config.DEFAULT_GUILD_ID or 1)
        inter.user.guild_permissions = MagicMock(administrator=True, manage_guild=True, manage_channels=True)
        inter.response.send_message = AsyncMock()
        with patch("bot.is_bot_owner", return_value=False), \
             patch("bot.DEFAULT_GUILD_ID", str(inter.guild.id)):
            await cmd_admin.callback(inter, action="stats")
        self.assertIn("權限不足", inter.response.send_message.call_args[0][0])


# ---------------------------------------------------------------------------
# F05 / N2 / N3 / N5 / F17: Telegram
# ---------------------------------------------------------------------------
class TestTelegramFixes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = MagicMock(spec=TelegramAPIClient)
        self.api.send_message = AsyncMock(return_value={"ok": True, "result": {"message_id": 1}})
        self.api.answer_callback_query = AsyncMock(return_value={"ok": True})
        self.api.get_chat_member = AsyncMock(return_value={"ok": True, "result": {"status": "member"}})
        self.storage = MagicMock()
        self.storage.get_or_create_subscriber.return_value = {"id": 7, "scope": "guild", "enabled": 1}
        self.bot = TelegramBot(token="t", storage=self.storage, api_client=self.api, admin_user_ids=["42"])

    def group_msg(self, uid, text):
        return {"chat": {"id": -100, "type": "supergroup", "title": "g"}, "from": {"id": uid}, "text": text}

    async def test_group_member_cannot_modify(self):
        for cmd in ("/pause", "/resume", "/keyword_add 3060"):
            await self.bot._handle_message(self.group_msg(5, cmd))
        self.storage.set_subscriber_enabled.assert_not_called()
        self.storage.add_keyword.assert_not_called()
        cb = {"id": "c", "data": "del_kw:1", "from": {"id": 5},
              "message": {"chat": {"id": -100, "type": "supergroup"}}}
        await self.bot._handle_callback_query(cb)
        self.storage.remove_keyword.assert_not_called()

    async def test_group_admin_and_configured_admin_can_modify(self):
        self.api.get_chat_member.return_value = {"ok": True, "result": {"status": "administrator"}}
        await self.bot._handle_message(self.group_msg(5, "/pause"))
        await self.bot._handle_message(self.group_msg(42, "/resume"))
        self.assertEqual(self.storage.set_subscriber_enabled.call_count, 2)

    def test_parse_keyword_add_args(self):
        self.assertEqual(parse_keyword_add_args(["3060Ti", "台北"])[:4], ("HardwareSale", "賣", "3060Ti 台北", None))
        self.assertEqual(parse_keyword_add_args(["3060Ti", "8000"])[:4], ("HardwareSale", "賣", "3060Ti", 8000))
        self.assertEqual(parse_keyword_add_args(["HardwareSale", "賣", "3060", "Ti", "7000"])[:4],
                         ("HardwareSale", "賣", "3060 Ti", 7000))
        self.assertEqual(parse_keyword_add_args(["macshop", "macbook"])[:3], ("MacShop", "賣", "macbook"))
        self.assertTrue(parse_keyword_add_args(["8000"])[2] == "8000")

    async def test_unknown_board_is_verified_before_adding(self):
        self.storage.get_or_create_subscriber.return_value = {"id": 7, "scope": "user", "enabled": 1}
        self.storage.is_board_tracked.return_value = False
        self.bot.crawler = MagicMock()
        self.bot.crawler.verify_board = AsyncMock(return_value=(False, "Nope", "找不到看板"))
        msg = {"chat": {"id": 1, "type": "private"}, "from": {"id": 1},
               "text": "/keyword_add Nope 賣 3060"}
        await self.bot._handle_message(msg)
        self.storage.add_keyword.assert_not_called()

    async def test_overflow_failure_not_recorded(self):
        storage = MagicMock()
        api = MagicMock()
        api.send_message = AsyncMock(side_effect=lambda **kw: (
            {"ok": True, "result": {"message_id": 1}} if "reply_markup" in kw
            else {"ok": False, "error_code": 500}))
        prov = TelegramNotificationProvider(api_client=api, storage=storage)
        now = int(time.time())
        items = [{"article": make_article(now, i, f"t{i}"), "detail": {}} for i in range(config.MAX_PUSH_PER_CYCLE_PER_SUBSCRIBER + 2)]
        with patch("telegram_bot.asyncio.sleep", AsyncMock()):
            await prov.deliver({"id": 3, "target_id": "1", "scope": "user"}, items)
        recorded = [c.kwargs.get("message_id") for c in storage.record_delivery.call_args_list]
        self.assertNotIn(None, recorded)

    async def test_bad_card_falls_back_and_does_not_block_batch(self):
        storage = MagicMock()
        api = MagicMock()
        calls = []

        async def send(**kw):
            calls.append(kw)
            if kw.get("parse_mode") == "HTML":
                return {"ok": False, "error_code": 400, "description": "can't parse entities"}
            return {"ok": True, "result": {"message_id": len(calls)}}

        api.send_message = send
        prov = TelegramNotificationProvider(api_client=api, storage=storage)
        now = int(time.time())
        items = [{"article": make_article(now, i, f"t{i}"), "detail": {}} for i in range(2)]
        with patch("telegram_bot.asyncio.sleep", AsyncMock()):
            pushed = await prov.deliver({"id": 3, "target_id": "1", "scope": "user"}, items)
        self.assertEqual(pushed, 2)

    async def test_one_unsendable_card_does_not_block_next(self):
        storage = MagicMock()
        api = MagicMock()
        now = int(time.time())
        items = [{"article": make_article(now, i, f"t{i}"), "detail": {}} for i in range(2)]
        bad_url = items[0]["article"]["url"]

        async def send(**kw):
            if bad_url in str(kw.get("reply_markup")):
                return {"ok": False, "error_code": 400, "description": "chat not found"}
            return {"ok": True, "result": {"message_id": 9}}

        api.send_message = send
        prov = TelegramNotificationProvider(api_client=api, storage=storage)
        with patch("telegram_bot.asyncio.sleep", AsyncMock()):
            pushed = await prov.deliver({"id": 3, "target_id": "1", "scope": "user"}, items)
        self.assertEqual(pushed, 1)

    def test_card_truncation_never_splits_entities(self):
        art = make_article(int(time.time()), 1, "[賣] A&B <x>")
        detail = {"snippet": "&<>" * 3000, "extracted_price": "$1&2"}
        card = format_article_card(art, ["k&k"], detail)
        self.assertLessEqual(len(card), 4000)
        self.assertEqual(card.count("<blockquote>"), card.count("</blockquote>"))
        import re as _re
        for m in _re.finditer(r"&", card):
            tail = card[m.start():m.start() + 6]
            self.assertRegex(tail, r"^&(amp|lt|gt|quot|#x27);")

    async def test_non_json_response_is_retried(self):
        client = TelegramAPIClient(token="t")
        bad = MagicMock(status=502)
        bad.json = AsyncMock(side_effect=ValueError("not json"))
        good = MagicMock(status=200)
        good.json = AsyncMock(return_value={"ok": True, "result": {}})
        responses = [bad, good]

        class _CM:
            def __init__(self, r):
                self.r = r

            async def __aenter__(self):
                return self.r

            async def __aexit__(self, *a):
                return False

        session = MagicMock()
        session.closed = False
        session.post = lambda *a, **k: _CM(responses.pop(0))
        client._session = session
        with patch("telegram_bot.asyncio.sleep", AsyncMock()):
            out = await client.request("getMe")
        self.assertTrue(out.get("ok"))


if __name__ == "__main__":
    unittest.main()
