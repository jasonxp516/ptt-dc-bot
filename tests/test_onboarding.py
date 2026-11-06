"""
PTT-DC-Bot Onboarding & Clean Boot Lifecycle Test Suite:
Clean boot lifecycle, owner resolution, deduplication, and exception paths in PTTDiscordBot.
"""
import os
import sys
import tempfile
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure repo root and isolated virtualenv site-packages are in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

VENV_SITE = "/tmp/test_venv/lib/python3.13/site-packages"
if os.path.exists(VENV_SITE) and VENV_SITE not in sys.path:
    sys.path.insert(0, VENV_SITE)

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# Safe default environment for tests to prevent writing to read-only repo root
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
from storage import BotStorage
from bot import PTTDiscordBot


class TestCleanBootLifecycleChallenge(unittest.TestCase):
    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        self.storage = BotStorage(self.temp_db_path)
        self.crawler = MagicMock()
        self.scheduler = MagicMock()
        self.agent = MagicMock()
        self.bot = PTTDiscordBot(
            storage=self.storage,
            crawler=self.crawler,
            scheduler=self.scheduler,
            agent=self.agent,
        )

    def tearDown(self):
        try:
            self.storage.close()
        except Exception:
            pass
        if os.path.exists(self.temp_db_path):
            try:
                os.unlink(self.temp_db_path)
            except Exception:
                pass

    # ---------------------------------------------------------
    # 1. Clean Boot Matrix
    # ---------------------------------------------------------
    def test_clean_boot_matrix_triggers_when_all_conditions_met(self):
        """0 push channels, 0 keywords, owner_welcome_sent != '1' -> triggers welcome DM."""
        # Baseline state: 0 push channels, 0 keywords, flag unset
        self.assertIsNone(self.storage.get_push_channel_id(1))
        self.assertEqual(len(self.storage.get_keywords()), 0)
        self.assertNotEqual(self.storage.get_setting("owner_welcome_sent"), "1")

        mock_owner = MagicMock(id=111, name="SoloDev")
        mock_owner.send = AsyncMock()
        self.bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))

        asyncio.run(self.bot._check_and_send_owner_welcome())

        # Verify DM sent with embed
        self.assertEqual(mock_owner.send.call_count, 1)
        _, kwargs = mock_owner.send.call_args
        embed = kwargs.get("embed")
        self.assertIsNotNone(embed)
        self.assertIn("歡迎使用 PTT-DC-Bot", embed.title)
        self.assertIn("/setup", embed.description)

        # Verify flag recorded
        self.assertEqual(self.storage.get_setting("owner_welcome_sent"), "1")
        # Verify detected_owner_id updated
        self.assertEqual(self.bot.detected_owner_id, "111")

    def test_clean_boot_matrix_skips_when_push_channel_configured(self):
        """push channels > 0 -> skips."""
        # Configure push channel
        self.storage.update_subscriber_channel(1, "987654321")
        self.assertIsNotNone(self.storage.get_push_channel_id(1))
        self.assertEqual(len(self.storage.get_keywords()), 0)
        self.assertNotEqual(self.storage.get_setting("owner_welcome_sent"), "1")

        mock_owner = MagicMock(id=111, name="SoloDev")
        mock_owner.send = AsyncMock()
        self.bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))

        asyncio.run(self.bot._check_and_send_owner_welcome())

        # Verify DM NOT sent
        self.assertEqual(mock_owner.send.call_count, 0)
        self.assertFalse(self.bot.application_info.called)
        # Verify flag remains unset
        self.assertNotEqual(self.storage.get_setting("owner_welcome_sent"), "1")

    def test_clean_boot_matrix_skips_when_keywords_configured(self):
        """keywords > 0 -> skips."""
        # Configure keyword
        self.storage.add_keyword("RTX 4090", subscriber_id=1)
        self.assertIsNone(self.storage.get_push_channel_id(1))
        self.assertGreater(len(self.storage.get_keywords()), 0)
        self.assertNotEqual(self.storage.get_setting("owner_welcome_sent"), "1")

        mock_owner = MagicMock(id=111, name="SoloDev")
        mock_owner.send = AsyncMock()
        self.bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))

        asyncio.run(self.bot._check_and_send_owner_welcome())

        # Verify DM NOT sent
        self.assertEqual(mock_owner.send.call_count, 0)
        self.assertFalse(self.bot.application_info.called)
        # Verify flag remains unset
        self.assertNotEqual(self.storage.get_setting("owner_welcome_sent"), "1")

    def test_clean_boot_matrix_skips_when_owner_welcome_sent_is_1(self):
        """owner_welcome_sent == '1' -> skips."""
        self.storage.set_setting("owner_welcome_sent", "1")
        self.assertIsNone(self.storage.get_push_channel_id(1))
        self.assertEqual(len(self.storage.get_keywords()), 0)
        self.assertEqual(self.storage.get_setting("owner_welcome_sent"), "1")

        mock_owner = MagicMock(id=111, name="SoloDev")
        mock_owner.send = AsyncMock()
        self.bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))

        asyncio.run(self.bot._check_and_send_owner_welcome())

        # Verify DM NOT sent
        self.assertEqual(mock_owner.send.call_count, 0)
        self.assertFalse(self.bot.application_info.called)

    def test_clean_boot_matrix_skips_when_both_channel_and_keywords_configured(self):
        """push channels > 0 and keywords > 0 -> skips."""
        self.storage.update_subscriber_channel(1, "987654321")
        self.storage.add_keyword("MacBook Pro", subscriber_id=1)

        mock_owner = MagicMock(id=111, name="SoloDev")
        mock_owner.send = AsyncMock()
        self.bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))

        asyncio.run(self.bot._check_and_send_owner_welcome())

        self.assertEqual(mock_owner.send.call_count, 0)
        self.assertFalse(self.bot.application_info.called)

    def test_clean_boot_matrix_triggers_when_flag_is_not_1(self):
        """owner_welcome_sent == '0' or '' -> triggers."""
        self.storage.set_setting("owner_welcome_sent", "0")
        mock_owner = MagicMock(id=111, name="SoloDev")
        mock_owner.send = AsyncMock()
        self.bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))

        asyncio.run(self.bot._check_and_send_owner_welcome())
        self.assertEqual(mock_owner.send.call_count, 1)
        self.assertEqual(self.storage.get_setting("owner_welcome_sent"), "1")

    # ---------------------------------------------------------
    # 2. Owner Resolution Matrix
    # ---------------------------------------------------------
    def test_owner_resolution_individual_owner(self):
        """Individual bot owner (app_info.owner)."""
        mock_owner = MagicMock(id=123456, name="IndividualDev")
        mock_owner.send = AsyncMock()
        app_info = MagicMock(owner=mock_owner, team=None)
        self.bot.application_info = AsyncMock(return_value=app_info)

        asyncio.run(self.bot._check_and_send_owner_welcome())

        self.assertEqual(mock_owner.send.call_count, 1)
        self.assertEqual(self.bot.detected_owner_id, "123456")

    def test_owner_resolution_team_owner(self):
        """Team bot owner (app_info.team.owner)."""
        team_owner = MagicMock(id=789012, name="TeamLead")
        team_owner.send = AsyncMock()
        team_mock = MagicMock(owner=team_owner)

        fallback_bot_user = MagicMock(id=999999, name="BotUser")
        fallback_bot_user.send = AsyncMock()

        app_info = MagicMock(owner=fallback_bot_user, team=team_mock)
        self.bot.application_info = AsyncMock(return_value=app_info)

        asyncio.run(self.bot._check_and_send_owner_welcome())

        # Must send to team.owner, NOT app_info.owner
        self.assertEqual(team_owner.send.call_count, 1)
        self.assertEqual(fallback_bot_user.send.call_count, 0)
        self.assertEqual(self.bot.detected_owner_id, "789012")

    def test_owner_resolution_does_not_clobber_existing_detected_owner_id(self):
        """If detected_owner_id was already configured, do not clobber it."""
        self.bot.detected_owner_id = "configured_id"
        mock_owner = MagicMock(id=999, name="ResolvedOwner")
        mock_owner.send = AsyncMock()
        self.bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))

        asyncio.run(self.bot._check_and_send_owner_welcome())

        self.assertEqual(mock_owner.send.call_count, 1)
        self.assertEqual(self.bot.detected_owner_id, "configured_id")

    # ---------------------------------------------------------
    # 3. Exception Isolation Matrix
    # ---------------------------------------------------------
    def test_exception_owner_send_raises_forbidden_50007(self):
        """owner.send() raises discord.Forbidden (code 50007) -> safely suppressed, flag recorded."""
        mock_owner = MagicMock(id=111, name="OwnerWithClosedDMs")
        # discord.Forbidden simulates 403 Forbidden with Discord code 50007
        mock_resp = MagicMock(status=403, reason="Forbidden")
        forbidden_exc = discord.Forbidden(
            response=mock_resp,
            message="Cannot send messages to this user (code 50007)"
        )
        mock_owner.send = AsyncMock(side_effect=forbidden_exc)
        self.bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))

        # Must not raise
        asyncio.run(self.bot._check_and_send_owner_welcome())

        # Flag MUST still be recorded to prevent repeated failing attempts
        self.assertEqual(self.storage.get_setting("owner_welcome_sent"), "1")

    def test_exception_owner_send_raises_generic_http_exception(self):
        """owner.send() raises discord.HTTPException -> safely suppressed, flag recorded."""
        mock_owner = MagicMock(id=111, name="Owner")
        mock_resp = MagicMock(status=500, reason="Internal Server Error")
        http_exc = discord.HTTPException(response=mock_resp, message="Internal Server Error")
        mock_owner.send = AsyncMock(side_effect=http_exc)
        self.bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))

        # Must not raise
        asyncio.run(self.bot._check_and_send_owner_welcome())

        self.assertEqual(self.storage.get_setting("owner_welcome_sent"), "1")

    def test_exception_application_info_network_error(self):
        """application_info network error -> safely suppressed."""
        mock_resp = MagicMock(status=503, reason="Gateway Timeout")
        self.bot.application_info = AsyncMock(
            side_effect=discord.HTTPException(response=mock_resp, message="Gateway Timeout")
        )

        # Must not raise
        asyncio.run(self.bot._check_and_send_owner_welcome())

        # Flag is recorded
        self.assertEqual(self.storage.get_setting("owner_welcome_sent"), "1")

    def test_exception_application_info_connection_reset_error(self):
        """application_info raises ConnectionResetError -> safely suppressed."""
        self.bot.application_info = AsyncMock(side_effect=ConnectionResetError("Connection lost"))

        asyncio.run(self.bot._check_and_send_owner_welcome())
        self.assertEqual(self.storage.get_setting("owner_welcome_sent"), "1")

    # ---------------------------------------------------------
    # 4. Persistence Across Reboots
    # ---------------------------------------------------------
    def test_persistence_across_reboots_simulation(self):
        """Simulate cold boot 1 -> restart -> cold boot 2 -> restart -> cold boot 3."""
        mock_owner = MagicMock(id=111, name="SoloDev")
        mock_owner.send = AsyncMock()

        # BOOT 1: Clean boot
        self.bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))
        asyncio.run(self.bot._check_and_send_owner_welcome())
        self.assertEqual(mock_owner.send.call_count, 1)
        self.assertEqual(self.storage.get_setting("owner_welcome_sent"), "1")

        # Simulate shutdown
        self.storage.close()

        # BOOT 2: Reboot with same database file
        storage_boot2 = BotStorage(self.temp_db_path)
        bot_boot2 = PTTDiscordBot(
            storage=storage_boot2,
            crawler=MagicMock(),
            scheduler=MagicMock(),
            agent=MagicMock(),
        )
        mock_owner_boot2 = MagicMock(id=111, name="SoloDev")
        mock_owner_boot2.send = AsyncMock()
        bot_boot2.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner_boot2, team=None))

        asyncio.run(bot_boot2._check_and_send_owner_welcome())

        # Must NOT send welcome DM again
        self.assertEqual(mock_owner_boot2.send.call_count, 0)
        self.assertFalse(bot_boot2.application_info.called)

        # BOOT 3: User configures push channel and reboot happens
        storage_boot2.update_subscriber_channel(1, "555555")
        storage_boot2.add_keyword("Switch OLED", subscriber_id=1)
        storage_boot2.close()

        storage_boot3 = BotStorage(self.temp_db_path)
        bot_boot3 = PTTDiscordBot(
            storage=storage_boot3,
            crawler=MagicMock(),
            scheduler=MagicMock(),
            agent=MagicMock(),
        )
        mock_owner_boot3 = MagicMock(id=111, name="SoloDev")
        mock_owner_boot3.send = AsyncMock()
        bot_boot3.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner_boot3, team=None))

        asyncio.run(bot_boot3._check_and_send_owner_welcome())
        self.assertEqual(mock_owner_boot3.send.call_count, 0)
        self.assertFalse(bot_boot3.application_info.called)
        storage_boot3.close()

    # ---------------------------------------------------------
    # 5. Multiple Gateway Reconnects (on_ready idempotency)
    # ---------------------------------------------------------
    def test_multiple_on_ready_reconnects_idempotent(self):
        """Simulate 5 gateway reconnects invoking on_ready(). Only 1 DM sent."""
        mock_owner = MagicMock(id=111, name="SoloDev")
        mock_owner.send = AsyncMock()
        self.bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))
        self.bot.tree.sync = AsyncMock(return_value=[])
        self.bot.change_presence = AsyncMock()
        self.bot._connection.user = MagicMock(id=999, name="PTTBot")

        async def simulate_reconnects():
            with patch.object(PTTDiscordBot, "guilds", new_callable=unittest.mock.PropertyMock, return_value=[]):
                for _ in range(5):
                    await self.bot.on_ready()

        asyncio.run(simulate_reconnects())

        # Exactly 1 DM sent across 5 on_ready triggers
        self.assertEqual(mock_owner.send.call_count, 1)
        self.assertEqual(self.bot.tree.sync.call_count, 1)


if __name__ == "__main__":
    unittest.main()
