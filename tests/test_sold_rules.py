"""
tests/test_sold_rules.py - Comprehensive tests for sold_rules.py, eval_sold, and embed updates.
"""

import os
import unittest

from embeds import build_article_embed
from sold_rules import (
    AVAILABLE,
    NO_MAIL,
    RESERVED,
    SOLD,
    classify_author_pushes,
    classify_text_state,
    classify_title_state,
    is_title_marked_sold,
    is_title_sold,
)
from tools.eval_sold import evaluate_sold_dataset


class TestSoldRulesPhase1(unittest.TestCase):
    """
    Phase 1.1 Table-driven cases & dataset evaluation tests.
    """

    def test_table_driven_cases_phase_1_1(self):
        """
        Verify all required table-driven cases from Phase 1.1:
        - Title `[賣/台北] 3060Ti 可洽中壢站` -> AVAILABLE
        - Title `[賣] ASUS XXX（原廠已停售款）` -> AVAILABLE
        - Title `[賣] 相機 結標時間 11/20` -> AVAILABLE
        - Title `[賣/台北] 3060Ti（已售出）` -> SOLD
        - Title `[賣/台北] 3060Ti 洽中` -> RESERVED
        - Author push `["尚未售出"]` -> AVAILABLE
        - Author push `["已售出，謝謝PTT"]` -> SOLD
        - Author push sequence `["洽談中", "交易取消，恢復販售"]` -> AVAILABLE
        - Body line `請勿來信詢問是否還在，直接站內信` -> AVAILABLE
        """
        # 1. Title false positives that MUST be AVAILABLE
        st, _ = classify_title_state("[賣/台北] 3060Ti 可洽中壢站")
        self.assertEqual(st, AVAILABLE, "可洽中壢站為面交地點，不得誤判為RESERVED或SOLD")

        st, _ = classify_title_state("[賣] ASUS XXX（原廠已停售款）")
        self.assertEqual(st, AVAILABLE, "原廠已停售款為商品背景描述，不得誤判為SOLD")

        st, _ = classify_title_state("[賣] 相機 結標時間 11/20")
        self.assertEqual(st, AVAILABLE, "結標時間為拍賣截止說明，不得誤判為SOLD")

        # 2. True SOLD title
        st, _ = classify_title_state("[賣/台北] 3060Ti（已售出）")
        self.assertEqual(st, SOLD, "標題標註（已售出）應判定為SOLD")

        # 3. True RESERVED title
        st, _ = classify_title_state("[賣/台北] 3060Ti 洽中")
        self.assertEqual(st, RESERVED, "標題末尾洽中應判定為RESERVED")

        # 4. Author push: negated sold marker -> AVAILABLE
        st, _ = classify_author_pushes(["尚未售出"])
        self.assertEqual(st, AVAILABLE, "尚未售出含否定前綴，不得判定為SOLD")

        # 5. Author push: true SOLD with thanks
        st, _ = classify_author_pushes(["已售出，謝謝PTT"])
        self.assertEqual(st, SOLD, "已售出謝謝PTT應判定為SOLD")

        # 6. Author push sequence: RESERVED then reopen -> AVAILABLE
        st, _ = classify_author_pushes(["洽談中", "交易取消，恢復販售"])
        self.assertEqual(st, AVAILABLE, "恢復販售應將先前洽談中狀態還原為AVAILABLE")

        # 7. Body line: trading instruction -> AVAILABLE
        st, _ = classify_text_state("請勿來信詢問是否還在，直接站內信", require_short_or_boundary=True)
        self.assertEqual(
            st,
            AVAILABLE,
            "『請勿來信詢問是否還在，直接站內信』為交易聯絡指引而非停止收信，不得判為NO_MAIL",
        )

    def test_additional_title_and_push_edge_cases(self):
        """Additional checks for locations, reopens, and thanks across different boards."""
        self.assertEqual(classify_title_state("[賣/新北] 螢幕 中和面交")[0], AVAILABLE)
        self.assertEqual(classify_title_state("[賣/台北] 鍵盤 洽中山區")[0], AVAILABLE)
        self.assertEqual(classify_title_state("[賣/雙北/皆可] 售出 ASUS RTX3060Ti")[0], SOLD)
        self.assertEqual(classify_title_state("[賣/台北] 技嘉 RTX 3060 12G 售出")[0], SOLD)
        self.assertEqual(classify_title_state("[賣] 撤售 iPhone 15")[0], SOLD)
        self.assertEqual(classify_title_state("[賣/新竹] PS5 Pro (洽談中)")[0], RESERVED)
        self.assertEqual(classify_title_state("[賣/台北] RTX 4070 (暫勿來信)")[0], NO_MAIL)

        # Helper boolean checks
        self.assertTrue(is_title_sold("[賣/台北] 3060Ti（已售出）"))
        self.assertTrue(is_title_marked_sold("[賣/台北] 3060Ti（已售出）"))
        self.assertFalse(is_title_sold("[賣/台北] 3060Ti 可洽中壢站"))
        self.assertFalse(is_title_marked_sold("[賣/台北] 3060Ti 可洽中壢站"))

        # Pushes
        self.assertEqual(classify_author_pushes(["還沒售出"])[0], AVAILABLE)
        self.assertEqual(classify_author_pushes(["售出，感謝硬體版"])[0], SOLD)
        self.assertEqual(classify_author_pushes(["感謝MacShop版"])[0], SOLD)
        self.assertEqual(classify_author_pushes(["暫售", "買家跑單，重新開賣"])[0], AVAILABLE)
        self.assertEqual(classify_author_pushes(["預定中"])[0], RESERVED)
        self.assertEqual(classify_author_pushes(["暫售"])[0], RESERVED)
        self.assertEqual(classify_author_pushes(["來信眾多，暫不回信"])[0], NO_MAIL)

    def test_evaluate_sold_dataset_accuracy_and_precision(self):
        """
        Evaluate tests/data/sold_labels.jsonl:
        - Must contain >= 60 records
        - Precision >= 0.95
        - Accuracy >= 0.95
        """
        dataset_path = os.path.join(
            os.path.dirname(__file__), "data", "sold_labels.jsonl"
        )
        self.assertTrue(
            os.path.exists(dataset_path), f"Dataset file missing at {dataset_path}"
        )

        metrics = evaluate_sold_dataset(dataset_path)

        self.assertGreaterEqual(
            metrics["total"],
            60,
            f"Dataset must have at least 60 records, found {metrics['total']}",
        )
        self.assertGreaterEqual(
            metrics["precision"],
            0.95,
            f"Precision must be >= 0.95, got {metrics['precision']:.4f}",
        )
        self.assertGreaterEqual(
            metrics["accuracy"],
            0.95,
            f"Accuracy must be >= 0.95, got {metrics['accuracy']:.4f}",
        )
        self.assertGreaterEqual(
            metrics["recall"],
            0.95,
            f"Recall must be >= 0.95, got {metrics['recall']:.4f}",
        )
        self.assertEqual(
            len(metrics["mismatches"]),
            0,
            f"Found unexpected mismatches: {metrics['mismatches']}",
        )

    def test_build_article_embed_sold_state(self):
        """
        Phase 1.1: Discord card title styling:
        - is_sold_update=True and sold_state='SOLD' adds '~~' strikethrough.
        - is_sold_update=True and sold_state='RESERVED' adds '[洽談中]' without '~~'.
        - is_sold_update=True and sold_state='NO_MAIL' adds '[暫勿來信]' without '~~'.
        """
        base_article = {
            "title": "[賣/台北] ASUS RTX 3060Ti 8G",
            "url": "https://www.ptt.cc/bbs/HardwareSale/M.1234567890.A.123.html",
            "board": "HardwareSale",
            "category": "賣",
            "author": "tester",
            "date": "11/10",
            "nrec": "15",
        }

        # 1. SOLD update
        embed_sold = build_article_embed(
            base_article,
            matched_keywords=["3060ti"],
            detail={"sold_state": SOLD},
            is_sold_update=True,
        )
        self.assertIn("~~", embed_sold.title, "SOLD 狀態卡片標題必須加上 ~~ 刪除線")
        self.assertIn("[已售出]", embed_sold.title)

        # 2. RESERVED update
        embed_reserved = build_article_embed(
            base_article,
            matched_keywords=["3060ti"],
            detail={"sold_state": RESERVED},
            is_sold_update=True,
        )
        self.assertNotIn(
            "~~", embed_reserved.title, "RESERVED 洽談中狀態卡片標題不得加上 ~~ 刪除線"
        )
        self.assertIn("[洽談中]", embed_reserved.title)

        # 3. NO_MAIL update
        embed_no_mail = build_article_embed(
            base_article,
            matched_keywords=["3060ti"],
            detail={"sold_state": NO_MAIL},
            is_sold_update=True,
        )
        self.assertNotIn("~~", embed_no_mail.title)
        self.assertIn("[暫勿來信]", embed_no_mail.title)


if __name__ == "__main__":
    unittest.main()
