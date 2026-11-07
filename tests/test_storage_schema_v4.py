import os
import sqlite3
import tempfile
import unittest

from storage import BotStorage


class TestStorageSchemaV4Migration(unittest.TestCase):
    """
    Schema v4 遷移與多平台訂閱者資料結構獨立單元測試套件。
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_v4.db")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_01_fresh_db_initializes_with_schema_v4(self):
        """情境 1: 全新資料庫初始化，自動建立 schema_version = 4 與 platform / target_id 欄位"""
        storage = BotStorage(db_path=self.db_path, default_channel_id="111222333")
        self.assertEqual(storage.get_setting("schema_version"), "4")

        # 檢驗預設伺服器訂閱者
        sub1 = storage.get_subscriber_by_id(1)
        self.assertIsNotNone(sub1)
        self.assertEqual(sub1["platform"], "discord")
        self.assertEqual(sub1["target_id"], "111222333")
        storage.close()

    def test_02_migration_from_v3_to_v4_with_data_preservation(self):
        """情境 2: 從現行 Schema v3 資料庫平滑升級，自動備份 .bak.v3 並精確轉移資料"""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cur.execute("INSERT INTO settings (key, value) VALUES ('schema_version', '3')")
        cur.execute(
            """
            CREATE TABLE subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL CHECK(scope IN ('user', 'guild')),
                discord_id TEXT NOT NULL,
                display_name TEXT DEFAULT '',
                push_channel_id TEXT,
                push_mode TEXT NOT NULL DEFAULT 'keyword',
                tier TEXT NOT NULL DEFAULT 'free',
                enabled INTEGER NOT NULL DEFAULT 1,
                dm_failed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(scope, discord_id)
            )
            """
        )
        # 插入 Guild (有 push_channel_id) 與 User (無 push_channel_id)
        cur.execute(
            "INSERT INTO subscribers (scope, discord_id, display_name, push_channel_id, push_mode, tier, created_at) "
            "VALUES ('guild', '999000111', '舊伺服器', '888777666', 'keyword', 'owner', '2026-11-01')"
        )
        cur.execute(
            "INSERT INTO subscribers (scope, discord_id, display_name, push_channel_id, push_mode, tier, created_at) "
            "VALUES ('user', '123456789', '舊個人', NULL, 'keyword', 'friend', '2026-11-02')"
        )
        conn.commit()
        conn.close()

        # 啟動 BotStorage 觸發遷移
        storage = BotStorage(db_path=self.db_path)
        self.assertEqual(storage.get_setting("schema_version"), "4")
        self.assertTrue(os.path.exists(f"{self.db_path}.bak.v3"), "必須產生 .bak.v3 備份檔")

        # 驗證 Guild 訂閱者 target_id 應採用 push_channel_id
        g_sub = storage.get_subscriber("guild", "999000111")
        self.assertIsNotNone(g_sub)
        self.assertEqual(g_sub["platform"], "discord")
        self.assertEqual(g_sub["target_id"], "888777666")

        # 驗證 User 訂閱者 target_id 應回退採用 discord_id
        u_sub = storage.get_subscriber("user", "123456789")
        self.assertIsNotNone(u_sub)
        self.assertEqual(u_sub["platform"], "discord")
        self.assertEqual(u_sub["target_id"], "123456789")
        storage.close()

    def test_03_multi_platform_add_and_list_subscribers(self):
        """情境 3: 使用 add_subscriber 新增 Discord, Telegram, Email 訂閱者，並以 list_subscribers 依平台篩選"""
        storage = BotStorage(db_path=self.db_path)

        # 新增 Telegram 訂閱者
        tg_sub = storage.add_subscriber(
            scope="user",
            target_id="987654321",
            platform="telegram",
            display_name="TG用戶Alice",
        )
        self.assertEqual(tg_sub["platform"], "telegram")
        self.assertEqual(tg_sub["target_id"], "987654321")

        # 新增 Email 訂閱者
        em_sub = storage.add_subscriber(
            scope="user",
            target_id="notify@example.com",
            platform="email",
            display_name="郵件通知群",
        )
        self.assertEqual(em_sub["platform"], "email")
        self.assertEqual(em_sub["target_id"], "notify@example.com")

        # 驗證 list_subscribers
        all_subs = storage.list_subscribers()
        self.assertGreaterEqual(len(all_subs), 3)  # default guild + tg + email

        tg_list = storage.list_subscribers(platform="telegram")
        self.assertEqual(len(tg_list), 1)
        self.assertEqual(tg_list[0]["target_id"], "987654321")

        em_list = storage.list_subscribers(platform="email")
        self.assertEqual(len(em_list), 1)
        self.assertEqual(em_list[0]["target_id"], "notify@example.com")

        dc_list = storage.list_subscribers(platform="discord")
        self.assertEqual(len(dc_list), 1)
        storage.close()

    def test_04_get_subscriber_backward_compatibility_and_lookup_modes(self):
        """情境 4: 驗證 get_subscriber 相容模式與多欄位定位模式"""
        storage = BotStorage(db_path=self.db_path)
        storage.add_subscriber(
            scope="user",
            target_id="tg_1001",
            platform="telegram",
            display_name="TG_User_1",
        )

        # 1. 舊有模式查無此人
        self.assertIsNone(storage.get_subscriber("user", "non_existent"))

        # 2. 舊有模式查預設伺服器
        default_sub = storage.get_subscriber("guild", storage.default_guild_id)
        self.assertIsNotNone(default_sub)

        # 3. 以 platform + target_id 精確查出 Telegram 訂閱者
        found = storage.get_subscriber(platform="telegram", target_id="tg_1001")
        self.assertIsNotNone(found)
        self.assertEqual(found["display_name"], "TG_User_1")
        storage.close()

    def test_05_get_all_enabled_subscribers_filtering(self):
        """情境 5: get_all_enabled_subscribers 支援可選 platform 過濾"""
        storage = BotStorage(db_path=self.db_path)
        storage.add_subscriber(scope="user", target_id="111", platform="telegram", enabled=True)
        storage.add_subscriber(scope="user", target_id="222", platform="telegram", enabled=False)
        storage.add_subscriber(scope="user", target_id="333", platform="email", enabled=True)

        # 全域已啟用
        enabled_all = storage.get_all_enabled_subscribers()
        self.assertEqual(len(enabled_all), 3)  # default guild + 1 tg + 1 email

        # 僅 Telegram 已啟用
        enabled_tg = storage.get_all_enabled_subscribers(platform="telegram")
        self.assertEqual(len(enabled_tg), 1)
        self.assertEqual(enabled_tg[0]["target_id"], "111")
        storage.close()

    def test_06_channel_update_synchronizes_target_id(self):
        """情境 6: 驗證 update_subscriber_target 與 update_subscriber_channel 同步更新 target_id"""
        storage = BotStorage(db_path=self.db_path)
        storage.update_subscriber_channel(1, "555666777")
        sub1 = storage.get_subscriber_by_id(1)
        self.assertEqual(sub1["push_channel_id"], "555666777")

        storage.update_subscriber_target(1, "999999999")
        sub1_updated = storage.get_subscriber_by_id(1)
        self.assertEqual(sub1_updated["target_id"], "999999999")
        storage.close()

    def test_07_indices_created_and_functional(self):
        """情境 7: 檢驗 idx_subscribers_platform_target 與 idx_subscribers_platform_scope 索引確實建立"""
        storage = BotStorage(db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        indices = {
            r[1]
            for r in conn.execute(
                "SELECT type, name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        self.assertIn("idx_subscribers_platform_target", indices)
        self.assertIn("idx_subscribers_platform_scope", indices)
        conn.close()
        storage.close()

    def test_08_forget_subscriber_by_id_cascades_cleanly(self):
        """情境 8: 驗證 forget_subscriber_by_id 乾淨級聯刪除多平台訂閱者資料"""
        storage = BotStorage(db_path=self.db_path)
        em_sub = storage.add_subscriber(scope="user", target_id="del@example.com", platform="email")
        sub_id = em_sub["id"]

        storage.add_keyword("RTX4090", board="HardwareSale", subscriber_id=sub_id)
        self.assertEqual(len(storage.get_keywords(subscriber_id=sub_id)), 1)

        ok = storage.forget_subscriber_by_id(sub_id)
        self.assertTrue(ok)
        self.assertIsNone(storage.get_subscriber_by_id(sub_id))
        self.assertEqual(len(storage.get_keywords(subscriber_id=sub_id)), 0)
        storage.close()


if __name__ == "__main__":
    unittest.main()
