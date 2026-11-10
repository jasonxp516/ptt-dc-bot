"""
tests/test_price_parse.py - Tests for price parsing, budget validation, and groq_agent budget integration.
"""

import asyncio
import json
import tempfile
import unittest

from groq_agent import GroqAgent
from ptt_crawler import PTTCrawler
from storage import BotStorage, extract_first_price_int, parse_budget


class TestPriceParse(unittest.TestCase):
    """
    Phase 1.2 & 2.5: Price extraction and budget parsing tests.
    """

    def test_extract_first_price_int_table_cases(self):
        """
        Verify extract_first_price_int correctly ignores hardware model tokens,
        date/year contexts, and parses explicit/fallback prices.
        """
        cases = [
            ("保固至 2026/03 售 5800", 5800),
            ("2025年購入 面交 6000元", 6000),
            ("RTX 3060 12G 售 5800", 5800),
            ("5.8k", 5800),
            ("i5-12400 + 3060Ti 共 15000", 15000),
            ("價格面議 2025", None),
            ("$5,800", 5800),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                actual = extract_first_price_int(text)
                self.assertEqual(
                    actual,
                    expected,
                    f"extract_first_price_int({text!r}) should be {expected}, got {actual}",
                )

    def test_parse_budget_valid_variants(self):
        """
        Verify parse_budget handles int, float, string currencies, and k/K notations.
        """
        budget_6500_inputs = [
            6500,
            6500.0,
            "6500",
            "6500.0",
            "6500元",
            "NT$6,500",
            "6.5k",
            "6.5K",
        ]
        for val in budget_6500_inputs:
            with self.subTest(val=val):
                self.assertEqual(
                    parse_budget(val),
                    6500,
                    f"parse_budget({val!r}) should evaluate to 6500",
                )

        # Zero handling (clearing budget)
        self.assertEqual(parse_budget(0), 0)
        self.assertEqual(parse_budget("0"), 0)

        # None / empty handling
        self.assertIsNone(parse_budget(None))
        self.assertIsNone(parse_budget(""))
        self.assertIsNone(parse_budget("   "))

    def test_parse_budget_invalid_raises_value_error(self):
        """
        Verify parse_budget raises ValueError on invalid strings or out-of-range amounts.
        """
        invalid_inputs = ["abc", "-100", "50", "9999999", "xyz123"]
        for inv in invalid_inputs:
            with self.subTest(inv=inv):
                with self.assertRaises(ValueError):
                    parse_budget(inv)

    def test_groq_agent_add_exact_keyword_budget_integration(self):
        """
        Verify GroqAgent._execute_tool('add_exact_keyword', ...) correctly parses budget:
        - "max_price": "6.5k" stores 6500
        - "max_price": 6500.0 stores 6500
        - "max_price": "invalid" returns {"success": False} without raising uncaught exception
        """
        with tempfile.NamedTemporaryFile() as tf:
            storage = BotStorage(tf.name)
            crawler = PTTCrawler()
            agent = GroqAgent(api_key="test_key", storage=storage, crawler=crawler)

            # 1. Test "max_price": "6.5k"
            step_log_1, out_json_1, _ = asyncio.run(
                agent._execute_tool(
                    "add_exact_keyword",
                    {"keyword": "3060ti", "board": "HardwareSale", "max_price": "6.5k"},
                    subscriber_id=1,
                    user_prompt="幫我追蹤 3060ti，預算 6.5k 內",
                    read_untrusted_ptt=False,
                    can_write=True,
                )
            )
            res1 = json.loads(out_json_1)
            self.assertTrue(res1.get("success"), f"Tool execution failed: {out_json_1}")
            kws = storage.get_keywords(subscriber_id=1)
            self.assertEqual(len(kws), 1)
            self.assertEqual(kws[0]["max_price"], 6500)

            # 2. Test "max_price": 6500.0
            step_log_2, out_json_2, _ = asyncio.run(
                agent._execute_tool(
                    "add_exact_keyword",
                    {"keyword": "4070", "board": "HardwareSale", "max_price": 6500.0},
                    subscriber_id=1,
                    user_prompt="追蹤 4070 預算 6500.0",
                    read_untrusted_ptt=False,
                    can_write=True,
                )
            )
            res2 = json.loads(out_json_2)
            self.assertTrue(res2.get("success"), f"Tool execution failed: {out_json_2}")
            kws_after = storage.get_keywords(subscriber_id=1)
            self.assertEqual(len(kws_after), 2)
            kw_4070 = [k for k in kws_after if k["keyword"] == "4070"][0]
            self.assertEqual(kw_4070["max_price"], 6500)

            # 3. Test "max_price": "invalid"
            step_log_3, out_json_3, _ = asyncio.run(
                agent._execute_tool(
                    "add_exact_keyword",
                    {"keyword": "4080", "board": "HardwareSale", "max_price": "invalid"},
                    subscriber_id=1,
                    user_prompt="追蹤 4080 預算 invalid",
                    read_untrusted_ptt=False,
                    can_write=True,
                )
            )
            res3 = json.loads(out_json_3)
            self.assertFalse(res3.get("success"))
            self.assertIn("error", res3)
            self.assertIn("無法解析預算參數", res3["error"])
            storage.close()

    def test_max_price_allows_none_extracted_price_and_embed_fallback(self):
        """
        Regression test (Phase 1.2 item 3): When extracted_price is None (or unparseable),
        a keyword rule with max_price must NOT filter out the article, and build_article_embed
        must display `價格請見內文`.
        """
        from embeds import build_article_embed

        extracted_price_int = extract_first_price_int(None)
        self.assertIsNone(extracted_price_int)
        max_p = 6000
        # Reproduce bot.py polling filter check
        filtered_out = extracted_price_int is not None and extracted_price_int > int(max_p)
        self.assertFalse(filtered_out)

        art = {
            "title": "[賣/台北] RTX 3060Ti 詳內文",
            "url": "https://www.ptt.cc/bbs/HardwareSale/M.1700000000.A.123.html",
            "board": "HardwareSale",
            "author": "seller",
            "date": "11/10",
            "category": "賣",
            "nrec": "2",
        }
        embed = build_article_embed(art, matched_keywords=["3060Ti"], detail={"extracted_price": None})
        price_fields = [f for f in embed.fields if f.name == "欲售價格"]
        self.assertEqual(len(price_fields), 1)
        self.assertEqual(price_fields[0].value, "`價格請見內文`")


if __name__ == "__main__":
    unittest.main()
