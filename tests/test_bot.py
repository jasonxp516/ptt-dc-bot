import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest

VENV_SITE = "/tmp/test_venv/lib/python3.13/site-packages"
if os.path.exists(VENV_SITE) and VENV_SITE not in sys.path:
    sys.path.insert(0, VENV_SITE)

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import config
_MODULE_TMPDIR = None
if not os.environ.get("LOG_PATH") or not os.environ.get("DB_PATH"):
    _MODULE_TMPDIR = tempfile.TemporaryDirectory()
    if not os.environ.get("LOG_PATH"):
        config.LOG_PATH = os.path.join(_MODULE_TMPDIR.name, "bot.log")
        os.environ["LOG_PATH"] = config.LOG_PATH
    if not os.environ.get("DB_PATH"):
        config.DB_PATH = os.path.join(_MODULE_TMPDIR.name, "bot_data.db")
        os.environ["DB_PATH"] = config.DB_PATH


def tearDownModule():
    global _MODULE_TMPDIR
    if _MODULE_TMPDIR is not None:
        try:
            _MODULE_TMPDIR.cleanup()
        except Exception:
            pass

import discord

from config import sanitize_user_input
from embeds import enforce_embed_limits
from groq_agent import GroqAgent
from groq_scheduler import GroqScheduler, parse_groq_duration
from ptt_crawler import (
    PTTCrawler,
    is_valid_board_name,
    is_valid_ptt_article_url,
)
from storage import (
    BotStorage,
    compute_dedupe_hash,
    extract_first_price_int,
    is_title_marked_sold,
)


class TestPTTBotUpgrades(unittest.TestCase):
    def test_v1_to_v3_migration_and_multitenant_isolation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "legacy.db")
            # Create a simulated v1 database with existing 3060TI, 6700XT, 3060 keywords
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO settings VALUES ('push_channel_id', '111111111111111111')"
            )
            conn.execute("INSERT INTO settings VALUES ('push_mode', 'keyword')")
            conn.execute(
                """
                CREATE TABLE keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL,
                    board TEXT NOT NULL,
                    category TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            for kw in ("3060TI", "6700XT", "3060"):
                conn.execute(
                    "INSERT INTO keywords (keyword, board, category, created_at) VALUES (?, 'HardwareSale', '賣', '2026-11-03')",
                    (kw,),
                )
            conn.commit()
            conn.close()

            # Initialize BotStorage -> should backup and migrate to schema v4 seamlessly
            storage = BotStorage(
                db_path=db_path,
                default_channel_id="111111111111111111",
                default_board="HardwareSale",
            )
            self.assertTrue(os.path.exists(f"{db_path}.bak"))
            self.assertEqual(storage.get_setting("schema_version"), "4")

            # Verify migrated keywords belong to subscriber #1 (default guild)
            sub1_kws = [k["keyword"] for k in storage.get_keywords(subscriber_id=1)]
            self.assertEqual(sub1_kws, ["3060TI", "6700XT", "3060"])

            # Verify read-only get_subscriber does NOT create a row (P2-3)
            self.assertIsNone(storage.get_subscriber("user", "999888777"))

            # Create a personal DM subscriber (friend) with flag (P3-3)
            friend_sub, is_new = storage.get_or_create_subscriber_with_flag(
                "user", "999888777", display_name="測試朋友A"
            )
            self.assertTrue(is_new)
            _, is_new_second = storage.get_or_create_subscriber_with_flag(
                "user", "999888777", display_name="測試朋友A"
            )
            self.assertFalse(is_new_second)
            self.assertEqual(friend_sub["scope"], "user")
            self.assertEqual(friend_sub["tier"], "friend")
            self.assertNotEqual(friend_sub["id"], 1)

            # Friend adds their own keyword with exclusion and max_price
            ok, _ = storage.add_keyword(
                "3060 -ti -筆電",
                board="HardwareSale",
                category="賣",
                subscriber_id=friend_sub["id"],
                max_price=5500,
            )
            self.assertTrue(ok)

            # Test keyword update (P3-5)
            friend_kw_id = storage.get_keywords(subscriber_id=friend_sub["id"])[0]["id"]
            ok_edit, _ = storage.update_keyword(
                str(friend_kw_id),
                subscriber_id=friend_sub["id"],
                new_max_price=6000,
            )
            self.assertTrue(ok_edit)
            self.assertEqual(
                storage.get_keywords(subscriber_id=friend_sub["id"])[0]["max_price"],
                6000,
            )

            # Verify strict isolation between subscriber #1 and friend_sub
            self.assertEqual(len(storage.get_keywords(subscriber_id=1)), 3)
            self.assertEqual(
                len(storage.get_keywords(subscriber_id=friend_sub["id"])), 1
            )
            self.assertIsNone(
                storage.remove_keyword("3060TI", subscriber_id=friend_sub["id"])
            )
            self.assertEqual(len(storage.get_keywords(subscriber_id=1)), 3)

            # Test matching with exclusions & numeric boundary
            hits_ti = storage.match_article_keywords_detailed(
                "HardwareSale", "[賣/台北] 華碩 RTX 3060Ti 8G 顯卡"
            )
            hit_sub_ids = {h["subscriber_id"] for h in hits_ti}
            self.assertIn(1, hit_sub_ids)
            self.assertNotIn(friend_sub["id"], hit_sub_ids)

            hits_pure = storage.match_article_keywords_detailed(
                "HardwareSale", "[賣/新竹] 微星 RTX 3060 12G 雙風扇"
            )
            hit_sub_ids_pure = {h["subscriber_id"] for h in hits_pure}
            self.assertIn(1, hit_sub_ids_pure)
            self.assertIn(friend_sub["id"], hit_sub_ids_pure)

            # Numeric boundary: '13060' should NOT match '3060'
            hits_false_num = storage.match_article_keywords_detailed(
                "HardwareSale", "[賣/台北] 全新 i5-13060 套裝機"
            )
            self.assertEqual(len(hits_false_num), 0)

            # Test conversation memory scoping (P2-4) and forget_subscriber cascade (P2-6)
            storage.append_conversation_memory(
                "999888777", "user", "個人對話", subscriber_id=friend_sub["id"]
            )
            storage.append_conversation_memory(
                "999888777", "user", "伺服器對話", subscriber_id=1
            )
            storage.append_conversation_memory(
                "111222333", "user", "其他使用者對話", subscriber_id=1
            )
            self.assertEqual(
                storage.get_conversation_memory(
                    "999888777", subscriber_id=friend_sub["id"]
                )[0]["content"],
                "個人對話",
            )
            self.assertEqual(
                storage.get_conversation_memory("999888777", subscriber_id=1)[0][
                    "content"
                ],
                "伺服器對話",
            )

            self.assertTrue(storage.forget_subscriber("user", "999888777"))
            self.assertEqual(
                len(storage.get_keywords(subscriber_id=friend_sub["id"])), 0
            )
            self.assertEqual(
                len(
                    storage.get_conversation_memory(
                        "999888777", subscriber_id=friend_sub["id"]
                    )
                ),
                0,
            )
            # Other user's memory on subscriber 1 must remain untouched
            self.assertEqual(
                len(storage.get_conversation_memory("111222333", subscriber_id=1)), 1
            )

            storage.close()

    def test_p0_1_board_reinit_never_resets_on_transient_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "board_test.db")
            storage = BotStorage(db_path=db_path, default_board="HardwareSale")

            # Initially uninitialized -> should_reinit_board is True
            b_init = storage.get_boards()[0]
            self.assertTrue(storage.should_reinit_board(b_init))

            # Complete baseline initialization
            storage.update_board_status(
                "HardwareSale", True, "已完成基準同步", scanned_now=True
            )
            b_ready = storage.get_boards()[0]
            self.assertFalse(storage.should_reinit_board(b_ready))

            # Simulate 3 consecutive PTT timeouts/503s (>15m outage) -> MUST NOT reset initialized!
            for _ in range(3):
                failures = storage.update_board_status(
                    "HardwareSale",
                    True,
                    "異常: HTTP 503",
                    scanned_now=False,
                    reset_baseline=False,
                    is_error=True,
                )
            self.assertEqual(failures, 3)
            b_after_err = storage.get_boards()[0]
            self.assertTrue(b_after_err["initialized"])
            self.assertFalse(storage.should_reinit_board(b_after_err))

            # Only explicit reset_baseline=True (e.g. 0 subscribers tracking the board) resets initialized
            storage.update_board_status(
                "HardwareSale",
                False,
                "待機中",
                scanned_now=False,
                reset_baseline=True,
            )
            b_idle = storage.get_boards()[0]
            self.assertFalse(b_idle["initialized"])
            self.assertTrue(storage.should_reinit_board(b_idle))
            storage.close()

    def test_p1_1_and_p1_2_multi_keyword_independent_views_and_sold_detection(self):
        crawler = PTTCrawler()

        # 1. Polite body phrase "誠意售出" + "謝謝PTT硬體版" should NOT be falsely marked sold!
        html_polite = """
        <div id="main-content" class="bbs-screen bbs-content">
        <div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">seller1 (賣家)</span></div>
        <div class="article-metaline"><span class="article-meta-tag">標題</span><span class="article-meta-value">[賣/台北] 華碩 RTX 3060TI 8G</span></div>
        <div class="article-metaline"><span class="article-meta-tag">時間</span><span class="article-meta-value">Tue Nov  3 10:00:00 2026</span></div>
        ◎欲售價格： 6,200 (價格暫定，可小議)
        ◎交易方式： 台北面交
        升級換下，功能正常，誠意售出，感謝PTT硬體版！
        --
        ※ 發信站: 批踢踢實業坊(ptt.cc)
        </div>
        """
        det_polite = crawler._parse_article_detail_sync(
            html_polite,
            article_title="[賣/台北] 華碩 RTX 3060TI 8G",
            article_author="seller1 (賣家)",
            target_keyword="3060TI",
        )
        # P1-4: "價格暫定" must NOT trigger sold!
        self.assertFalse(det_polite["is_sold"])
        self.assertIn("6,200", det_polite["extracted_price"])

        # 2. Author inline push below signature (like M.1792681775.A.736.html) MUST be caught as sold!
        html_sdd = """
        <div id="main-content" class="bbs-screen bbs-content">
        <div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">sddgoodboy (sdd)</span></div>
        <div class="article-metaline"><span class="article-meta-tag">標題</span><span class="article-meta-value">[賣/桃園] 技嘉 1060 / 華碩 3060ti</span></div>
        1. 技嘉 1060 6G：1500
        2. 華碩 DUAL RTX3060TI：6000
        --
        ※ 發信站: 批踢踢實業坊(ptt.cc)
        ※ 編輯: sddgoodboy (1.2.3.4), 11/02/2026 20:00:00
        → sddgoodboy: 已洽談，不用再來信囉，謝謝PPT 42.73.1.1 11/02 20:26
        ※ 編輯: sddgoodboy (1.2.3.4), 11/02/2026 20:30:00
        </div>
        """
        det_sdd = crawler._parse_article_detail_sync(
            html_sdd,
            article_title="[賣/桃園] 技嘉 1060 / 華碩 3060ti",
            article_author="sddgoodboy (sdd)",
            target_keyword="3060TI",
        )
        self.assertTrue(det_sdd["is_sold"])
        self.assertIn("已洽談", det_sdd["sold_reason"])

        # 3. Multi-item post: Base detail cached once, then evaluated for "1060" (sold) vs "3060TI" (unsold $5800)
        html_multi = """
        <div id="main-content" class="bbs-screen bbs-content">
        <div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">multiuser (M)</span></div>
        <div class="article-metaline"><span class="article-meta-tag">標題</span><span class="article-meta-value">[賣/台中] 1060 / 3060TI 顯卡出清</span></div>
        ◎欲售價格：
        1. GTX 1060 6G: 1500 (已售出)
        2. RTX 3060TI 8G: 5800 元
        ◎交易地點： 台中面交
        --
        ※ 發信站: 批踢踢實業坊(ptt.cc)
        </div>
        """
        base_det = crawler._parse_article_detail_sync(
            html_multi,
            article_title="[賣/台中] 1060 / 3060TI 顯卡出清",
            article_author="multiuser (M)",
            target_keyword="",
        )
        # Base detail is not whole-post sold because item #2 is still available
        self.assertFalse(base_det["is_sold"])

        # Applying view for "1060" -> MUST be marked sold!
        view_1060 = PTTCrawler.apply_keyword_view(base_det, "1060")
        self.assertTrue(view_1060["is_sold"])

        # Applying view for "3060TI" on the SAME base_det -> MUST be available at 5800!
        view_3060ti = PTTCrawler.apply_keyword_view(base_det, "3060TI")
        self.assertFalse(view_3060ti["is_sold"])
        self.assertEqual(
            extract_first_price_int(view_3060ti["extracted_price"]), 5800
        )

    def test_p1_3_and_p1_4_price_extraction_and_title_sold_patterns(self):
        # P1-3: Hardware model numbers must NOT be mistaken for prices
        self.assertEqual(extract_first_price_int("RTX 3060 12G 售 5800"), 5800)
        self.assertEqual(extract_first_price_int("i5-12400 + 3060Ti 共 15000"), 15000)
        self.assertEqual(extract_first_price_int("RX 6700XT 12GB $6,500"), 6500)
        self.assertEqual(extract_first_price_int("欲售 5.8k 可議"), 5800)

        # P1-4: Title sold patterns without brackets
        self.assertTrue(is_title_marked_sold("[賣/雙北/皆可] 售出 ASUS RTX3060Ti"))
        self.assertTrue(is_title_marked_sold("（售出）微星 RX 6700 XT 12G"))
        self.assertTrue(is_title_marked_sold("[賣/台北] 技嘉 RTX 3060 12G 售出"))
        self.assertFalse(is_title_marked_sold("[賣/台北] 華碩 RTX 3060Ti 8G 正常使用"))

    def test_p1_5_delivery_sold_check_round_robin_ordering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "delivery_rr.db")
            storage = BotStorage(db_path=db_path)

            for i in (1, 2, 3):
                storage.record_delivery(
                    subscriber_id=1,
                    article={
                        "url": f"https://www.ptt.cc/bbs/HardwareSale/M.{i}.html",
                        "board": "HardwareSale",
                        "author": f"seller{i}",
                        "title": f"[賣/台北] 顯卡 #{i}",
                    },
                    channel_id="123456",
                    message_id=f"99900{i}",
                )

            batch1 = storage.get_recent_unmarked_deliveries(limit=3)
            urls1 = [r["article_url"] for r in batch1]
            # Touch the first checked URL -> it should rotate to the end on the next query
            first_url = urls1[0]
            storage.touch_delivery_sold_check(first_url)

            batch2 = storage.get_recent_unmarked_deliveries(limit=3)
            urls2 = [r["article_url"] for r in batch2]
            self.assertEqual(urls2[-1], first_url)
            self.assertNotEqual(urls2[0], first_url)
            storage.close()

    def test_discord_formatter_and_embed_limits(self):
        raw_md = (
            "#### 掃描結果\n"
            "| 商品 | 價格 | 狀態 |\n"
            "| --- | --- | --- |\n"
            "| RTX 3060Ti | 6000元 | 販售中 |\n"
            "---\n"
            "完成<br>謝謝"
        )
        formatted = GroqAgent.format_for_discord(raw_md)
        self.assertNotIn("| --- |", formatted)
        self.assertIn(
            "• **商品**：RTX 3060Ti ｜ **價格**：6000元 ｜ **狀態**：販售中", formatted
        )
        self.assertIn("**掃描結果**", formatted)
        self.assertNotIn("<br>", formatted)

        # Test embed 6000-char limit guard
        huge_embed = discord.Embed(title="T" * 300, description="D" * 4500)
        for i in range(10):
            huge_embed.add_field(name=f"Field {i}", value="X" * 1200)
        safe = enforce_embed_limits(huge_embed)
        total = (
            len(safe.title or "")
            + len(safe.description or "")
            + sum(len(f.name) + len(f.value) for f in safe.fields)
        )
        self.assertLessEqual(total, 5800)

    def test_scheduler_and_security_helpers(self):
        self.assertAlmostEqual(parse_groq_duration("500ms"), 0.5)
        self.assertAlmostEqual(parse_groq_duration("1m26.4s"), 86.4)
        self.assertEqual(sanitize_user_input("3060 @everyone <@123456>"), "3060")
        self.assertEqual(
            compute_dedupe_hash("HardwareSale", "abc", "[賣/台北] RTX 3060Ti"),
            compute_dedupe_hash("hardwaresale", "ABC", "[賣/台北]RTX-3060ti"),
        )

        sched = GroqScheduler(api_key="test")
        self.assertTrue(sched.acquire_user_slot("u1", "ai"))
        self.assertFalse(sched.acquire_user_slot("u1", "ai"))
        sched.release_user_slot("u1", "ai")
        self.assertTrue(sched.acquire_user_slot("u1", "ai"))

    def test_p0_2_read_only_permission_and_prompt_injection_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            storage = BotStorage(db_path=db_path, default_channel_id="123")
            crawler = PTTCrawler(storage=storage)
            agent = GroqAgent(api_key="test", storage=storage, crawler=crawler)

            storage.add_keyword("3060TI", subscriber_id=1)

            # 1. P0-2: Unprivileged guild member (`can_write=False`) cannot add or remove rules via AI agent
            step_log_ro, out_ro, _ = asyncio.run(
                agent._execute_tool(
                    "add_exact_keyword",
                    {"keyword": "4090", "board": "HardwareSale"},
                    subscriber_id=1,
                    user_prompt="幫伺服器新增追蹤 4090",
                    read_untrusted_ptt=False,
                    can_write=False,
                )
            )
            self.assertIn("權限不足", step_log_ro)
            self.assertEqual(len(storage.get_keywords(subscriber_id=1)), 1)

            # 2. Prompt injection guard blocks unrequested delete after reading untrusted PTT content
            step_log, out_json, _ = asyncio.run(
                agent._execute_tool(
                    "remove_exact_keyword",
                    {"identifier": "3060TI"},
                    subscriber_id=1,
                    user_prompt="幫我看看現在版上有沒有 3060TI",
                    read_untrusted_ptt=True,
                    can_write=True,
                )
            )
            self.assertIn("安全防護攔截", step_log)
            self.assertEqual(len(storage.get_keywords(subscriber_id=1)), 1)

            # 3. SSRF & Path Traversal protection on URLs and board names
            self.assertTrue(
                is_valid_ptt_article_url(
                    "https://www.ptt.cc/bbs/HardwareSale/M.1792681775.A.736.html"
                )
            )
            self.assertFalse(is_valid_ptt_article_url("http://127.0.0.1:8080/admin"))
            self.assertFalse(
                is_valid_ptt_article_url("https://www.ptt.cc/bbs/../../etc/passwd")
            )
            self.assertTrue(is_valid_board_name("HardwareSale"))
            self.assertTrue(is_valid_board_name("nb-shopping"))
            self.assertFalse(is_valid_board_name("../../etc"))

            ssrf_step, ssrf_out, _ = asyncio.run(
                agent._execute_tool(
                    "read_article_detail",
                    {"article_url": "http://169.254.169.254/latest/meta-data/"},
                    subscriber_id=1,
                    user_prompt="讀這個網址",
                    read_untrusted_ptt=False,
                    can_write=True,
                )
            )
            self.assertIn("安全防護攔截", ssrf_step)
            self.assertIn("僅允許讀取合法的 PTT 文章網址", ssrf_out)

            # 4. AI tool `scan_unsold_articles` enforces daily_scans quota
            ext_guild = storage.get_or_create_subscriber("guild", "888888888")
            # Exhaust `free` tier's 8 daily scans
            for _ in range(8):
                allowed, _, _ = storage.check_and_increment_usage(
                    ext_guild["id"], "scan"
                )
                self.assertTrue(allowed)

            quota_step, quota_out, _ = asyncio.run(
                agent._execute_tool(
                    "scan_unsold_articles",
                    {"board": "HardwareSale", "keywords": ["3060TI"]},
                    subscriber_id=ext_guild["id"],
                    user_prompt="幫我掃描未售出的 3060TI",
                    read_untrusted_ptt=False,
                    can_write=True,
                )
            )
            self.assertIn("已達今日歷史未售出掃描次數上限", quota_step)
            storage.close()

    def test_smart_board_routing_aliases_and_autocomplete(self):
        from bot import _build_ptt_board_choices
        from ptt_crawler import infer_board_from_query, resolve_board_alias

        self.assertEqual(infer_board_from_query("我想買 dji neo"), "DC_Sale")
        self.assertEqual(infer_board_from_query("想收 Sony A7C2 鏡頭"), "DC_Sale")
        self.assertEqual(infer_board_from_query("MacBook Air M3"), "MacShop")
        self.assertEqual(infer_board_from_query("ThinkPad X1 二手筆電"), "nb-shopping")
        self.assertEqual(infer_board_from_query("Switch 薩爾達"), "Gamesale")
        self.assertEqual(infer_board_from_query("二手手機 S24 Ultra"), "mobilesales")
        self.assertEqual(infer_board_from_query("WH-1000XM5 耳機"), "Headphone")
        self.assertEqual(
            infer_board_from_query("3060 -ti -筆電"), "HardwareSale"
        )

        self.assertEqual(resolve_board_alias("相機"), "DC_Sale")
        self.assertEqual(resolve_board_alias("空拍機"), "DC_Sale")
        self.assertEqual(resolve_board_alias("筆電"), "nb-shopping")
        self.assertEqual(resolve_board_alias("蘋果"), "MacShop")
        self.assertEqual(resolve_board_alias("電玩"), "Gamesale")
        self.assertEqual(
            resolve_board_alias(
                "DC_Sale — 數位相機與攝影器材二手買賣板 [已監控]"
            ),
            "DC_Sale",
        )

        dji_choices = _build_ptt_board_choices("dji")
        self.assertIsInstance(dji_choices, list)
        self.assertTrue(any(choice.value == "DC_Sale" for choice in dji_choices))
        all_choices = _build_ptt_board_choices("", include_all_option=True)
        self.assertEqual(all_choices[0].value, "ALL")

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "routing.db")
            storage = BotStorage(db_path=db_path)
            crawler = PTTCrawler(storage=storage)

            async def _mock_verify(b_in):
                return True, resolve_board_alias(b_in), "驗證成功"

            crawler.verify_board = _mock_verify
            agent = GroqAgent(api_key="test", storage=storage, crawler=crawler)
            step_log, out_json, _ = asyncio.run(
                agent._execute_tool(
                    "add_exact_keyword",
                    {"keyword": "dji neo"},
                    subscriber_id=1,
                    user_prompt="我想買 dji neo",
                    read_untrusted_ptt=True,
                    can_write=True,
                )
            )
            self.assertIn("DC_Sale", step_log)
            kws = storage.get_keywords(subscriber_id=1)
            self.assertEqual(len(kws), 1)
            self.assertEqual(kws[0]["board"], "DC_Sale")
            storage.close()


if __name__ == "__main__":
    unittest.main()
