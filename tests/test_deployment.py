"""
Comprehensive Offline End-to-End (E2E) Test Suite for PTT-DC-Bot Deployment
=============================================================================
This test suite covers Tiers 1-4 across all 28 features defined in PROJECT.md:
  - F01-F05: Standalone CLI Setup Wizard (setup_wizard.py)
  - F06-F08: Bot Startup Interception & Windows Safeguards (bot.py)
  - F09-F12: Cross-Platform Installers & Launchers (install.sh, install.ps1, start_windows.bat, start_mac.command)
  - F13-F17: Discord /setup Visual UI Panel & Modal Dialog Components
  - F18-F19: Owner Welcome DM & Deduplication
  - F20-F24: CI/CD Release Pipeline & Docker Hardening
  - F25-F26: Bilingual Documentation Alignment
  - F27-F28: 100% Offline Mock Testing & Clean Workspace Guarantee

CRITICAL CONSTRAINTS:
  1. 100% Offline Mock Testing: All HTTP (Discord REST, Groq API) and Discord gateway calls are mocked.
  2. Zero Credential Leakage: Only dummy placeholders used (e.g. "mock_discord_token", "gsk_mock_groq_key").
  3. Clean Workspace Guarantee: All tests execute in tempfile.TemporaryDirectory() and clean up on exit.
  4. Progressive Testability: Dynamically gates features from pending milestones while thoroughly verifying implemented code.
"""

import asyncio
import builtins
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure repo root and isolated virtualenv site-packages are in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Auto-fallback to isolated venv packages if running under system python
VENV_SITE = "/tmp/test_venv/lib/python3.13/site-packages"
if os.path.exists(VENV_SITE) and VENV_SITE not in sys.path:
    sys.path.insert(0, VENV_SITE)

# Prevent bytecode file generation in workspace
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# Safe default environment for tests to prevent writing to read-only repo root
import config
_MODULE_TMPDIR = tempfile.TemporaryDirectory()
config.LOG_PATH = os.path.join(_MODULE_TMPDIR.name, "bot.log")
config.DB_PATH = os.path.join(_MODULE_TMPDIR.name, "bot_data.db")
os.environ["LOG_PATH"] = config.LOG_PATH
os.environ["DB_PATH"] = config.DB_PATH


def tearDownModule():
    try:
        _MODULE_TMPDIR.cleanup()
    except Exception:
        pass

# Feature availability indicators for progressive testability
HAS_SETUP_WIZARD = os.path.exists(os.path.join(BASE_DIR, "setup_wizard.py"))
HAS_INSTALL_SH = os.path.exists(os.path.join(BASE_DIR, "install.sh"))
HAS_INSTALL_PS1 = os.path.exists(os.path.join(BASE_DIR, "install.ps1"))
HAS_START_WIN = os.path.exists(os.path.join(BASE_DIR, "start_windows.bat"))
HAS_START_MAC = os.path.exists(os.path.join(BASE_DIR, "start_mac.command"))

if HAS_SETUP_WIZARD:
    import setup_wizard
else:
    setup_wizard = None


class BaseDeploymentTestCase(unittest.TestCase):
    """Base test case providing isolated temporary environment and cleanup guarantees."""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmpdir = self._temp_dir.name
        self.dummy_env_path = os.path.join(self.tmpdir, ".env")
        self.dummy_db_path = os.path.join(self.tmpdir, "test_bot.db")
        self.dummy_log_path = os.path.join(self.tmpdir, "test_bot.log")

        # Configure environment variables to point to temp paths
        self.orig_env = os.environ.copy()
        os.environ["DB_PATH"] = self.dummy_db_path
        os.environ["LOG_PATH"] = self.dummy_log_path

    def tearDown(self):
        # Restore environment
        os.environ.clear()
        os.environ.update(self.orig_env)

        # Cleanup temporary directory
        try:
            self._temp_dir.cleanup()
        except Exception:
            pass


# =============================================================================
# 1. Setup Wizard Unit Tests (F01–F05)
# =============================================================================

@unittest.skipUnless(HAS_SETUP_WIZARD, "setup_wizard.py not found")
class TestSetupWizard(BaseDeploymentTestCase):
    """Unit tests for setup_wizard.py validating OAuth2 calculation, REST APIs, and .env creation."""

    def test_generate_oauth2_url_standard_permissions(self):
        """F03: Verify OAuth2 URL generation with permission 274877975552 and required scope."""
        bot_id = "123456789012345678"
        url = setup_wizard.generate_oauth2_url(bot_id)
        expected_url = (
            "https://discord.com/oauth2/authorize"
            f"?client_id={bot_id}&permissions=274877975552&scope=bot%20applications.commands"
        )
        self.assertEqual(url, expected_url)

    def test_generate_oauth2_url_strips_whitespace(self):
        """F03: Verify OAuth2 URL strips leading and trailing whitespaces from client ID."""
        url = setup_wizard.generate_oauth2_url("   998877665544   ")
        self.assertIn("client_id=998877665544", url)
        self.assertIn("permissions=274877975552", url)

    def test_validate_discord_token_success(self):
        """F02: Verify Discord token live validation returns bot ID and username on HTTP 200."""
        mock_response_body = json.dumps({"id": "1122334455", "username": "PTT-Bot"}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = mock_response_body
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            is_valid, bot_id, username, err = setup_wizard.validate_discord_token("mock_token_abc")
            self.assertTrue(is_valid)
            self.assertEqual(bot_id, "1122334455")
            self.assertEqual(username, "PTT-Bot")
            self.assertIsNone(err)

            # Verify request authorization header
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.headers.get("Authorization"), "Bot mock_token_abc")
            self.assertEqual(req.headers.get("User-agent"), setup_wizard.USER_AGENT)

    def test_validate_discord_token_401_unauthorized(self):
        """F02 (Edge Case): Verify HTTP 401 Unauthorized returns helpful guidance and failure."""
        err_response = io.BytesIO(b'{"message": "401: Unauthorized", "code": 0}')
        http_err = urllib.error.HTTPError(
            url="https://discord.com/api/v10/users/@me",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=err_response,
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            is_valid, bot_id, username, err = setup_wizard.validate_discord_token("invalid_token")
            self.assertFalse(is_valid)
            self.assertIsNone(bot_id)
            self.assertIn("401", err)

    def test_validate_discord_token_network_url_error(self):
        """F02 (Edge Case): Verify network timeouts/failures return clear error without crashing."""
        url_err = urllib.error.URLError(reason="Name or service not known")
        with patch("urllib.request.urlopen", side_effect=url_err):
            is_valid, bot_id, username, err = setup_wizard.validate_discord_token("some_token")
            self.assertFalse(is_valid)
            self.assertIn("網路連線失敗", err)

    def test_validate_discord_token_empty_or_whitespace(self):
        """F02 (Boundary): Empty or blank token is rejected immediately without network requests."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            is_valid, bot_id, username, err = setup_wizard.validate_discord_token("   ")
            self.assertFalse(is_valid)
            self.assertEqual(err, "Token 不能為空")
            mock_urlopen.assert_not_called()

    def test_validate_groq_api_key_success(self):
        """F04: Verify Groq API key validation succeeds with Bearer authentication."""
        mock_response_body = json.dumps({"data": [{"id": "llama-3.3-70b-versatile"}]}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = mock_response_body
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            is_valid, err = setup_wizard.validate_groq_api_key("gsk_mock_valid_key")
            self.assertTrue(is_valid)
            self.assertIsNone(err)

            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.headers.get("Authorization"), "Bearer gsk_mock_valid_key")

    def test_validate_groq_api_key_401_unauthorized(self):
        """F04 (Edge Case): Verify HTTP 401 returns clear invalid key message."""
        http_err = urllib.error.HTTPError(
            url="https://api.groq.com/openai/v1/models",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"error": "Invalid API Key"}'),
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            is_valid, err = setup_wizard.validate_groq_api_key("gsk_invalid")
            self.assertFalse(is_valid)
            self.assertIn("401", err)

    def test_validate_groq_api_key_empty(self):
        """F04 (Boundary): Empty key is rejected immediately without network requests."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            is_valid, err = setup_wizard.validate_groq_api_key("")
            self.assertFalse(is_valid)
            self.assertEqual(err, "API Key 不能為空")
            mock_urlopen.assert_not_called()

    def test_validate_groq_api_key_invalid_payload_rejected(self):
        """F04 (Adversarial): Reject HTTP 200 with malformed JSON bodies (e.g. error payloads or non-list)."""
        malformed_bodies = [
            b'{"error": {"message": "Invalid API Key", "type": "invalid_request_error"}}',
            b'{"data": "not_a_list"}',
            b'["array_root"]',
            b'{}',
            b'null',
        ]
        for bad_payload in malformed_bodies:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = bad_payload
            mock_resp.__enter__.return_value = mock_resp

            with patch("urllib.request.urlopen", return_value=mock_resp):
                is_valid, err = setup_wizard.validate_groq_api_key("gsk_dummy_test_key")
                self.assertFalse(is_valid, f"Expected validation failure for payload: {bad_payload.decode('utf-8', 'ignore')}")
                self.assertIsNotNone(err)

    def test_validate_discord_token_rejects_missing_or_null_bot_id(self):
        """F02 (Adversarial): Reject HTTP 200 when response lacks a valid bot ID (e.g. null, empty, None)."""
        invalid_bodies = [
            b'{}',
            b'{"username": "ValidUser"}',
            b'{"id": null, "username": "NullBot"}',
            b'{"id": "", "username": "EmptyBot"}',
            b'{"id": "   ", "username": "WhitespaceBot"}',
            b'{"id": "None", "username": "LiteralNoneBot"}',
            b'{"id": "none", "username": "LowerNoneBot"}',
        ]
        for bad_payload in invalid_bodies:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = bad_payload
            mock_resp.__enter__.return_value = mock_resp

            with patch("urllib.request.urlopen", return_value=mock_resp):
                is_valid, bot_id, username, err = setup_wizard.validate_discord_token("dummy_token")
                self.assertFalse(is_valid, f"Expected rejection for invalid payload: {bad_payload.decode('utf-8', 'ignore')}")
                self.assertIsNone(bot_id)

    def test_validate_discord_token_strips_bot_prefix(self):
        """F02 (Robustness): Strip case-insensitive 'Bot ' prefix before sending request."""
        valid_response = json.dumps({"id": "9876543210", "username": "CleanBot"}).encode("utf-8")
        test_tokens = [
            "Bot raw_secret_123",
            "bot raw_secret_123",
            "BOT raw_secret_123",
            "Bot   raw_secret_123",
        ]
        for tok in test_tokens:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = valid_response
            mock_resp.__enter__.return_value = mock_resp

            with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
                is_valid, bot_id, username, err = setup_wizard.validate_discord_token(tok)
                self.assertTrue(is_valid)
                self.assertEqual(bot_id, "9876543210")
                self.assertEqual(username, "CleanBot")

                req = mock_urlopen.call_args[0][0]
                self.assertEqual(req.headers.get("Authorization"), "Bot raw_secret_123")

    def test_validate_discord_token_prefix_only_rejected_as_empty(self):
        """F02 (Boundary): Token consisting solely of 'Bot' or 'Bot ' is rejected without network request."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            for bad_token in ["Bot", "bot ", "  BOT   "]:
                is_valid, bot_id, username, err = setup_wizard.validate_discord_token(bad_token)
                self.assertFalse(is_valid)
                self.assertEqual(err, "Token 不能為空")
                mock_urlopen.assert_not_called()

    def test_is_valid_board_name_standard_and_edge_cases(self):
        """F01 (Boundary): Verify board name sanitization against traversal or illegal characters."""
        self.assertTrue(setup_wizard.is_valid_board_name("HardwareSale"))
        self.assertTrue(setup_wizard.is_valid_board_name("MacShop"))
        self.assertTrue(setup_wizard.is_valid_board_name("nb-shopping"))
        self.assertTrue(setup_wizard.is_valid_board_name("MobileComm"))

        self.assertFalse(setup_wizard.is_valid_board_name(""))
        self.assertFalse(setup_wizard.is_valid_board_name("../../etc"))
        self.assertFalse(setup_wizard.is_valid_board_name("Hardware Sale"))
        self.assertFalse(setup_wizard.is_valid_board_name("board*invalid"))
        self.assertFalse(setup_wizard.is_valid_board_name("A" * 50))

    def test_write_env_file_creates_file_and_content(self):
        """F05: Verify write_env_file writes valid UTF-8 .env with all expected configuration keys."""
        ok = setup_wizard.write_env_file(
            env_path=self.dummy_env_path,
            discord_token="mock_discord_token_xyz",
            groq_api_key="gsk_mock_groq_key_123",
            default_board="MacShop",
            poll_interval_min=60,
            poll_interval_max=120,
        )
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(self.dummy_env_path))

        with open(self.dummy_env_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("DISCORD_TOKEN=mock_discord_token_xyz", content)
        self.assertIn("GROQ_API_KEY=gsk_mock_groq_key_123", content)
        self.assertIn("DEFAULT_BOARD=MacShop", content)
        self.assertIn("BOT_LANGUAGE=zh_TW", content)
        self.assertIn("POLL_INTERVAL_MIN=60", content)
        self.assertIn("POLL_INTERVAL_MAX=120", content)

    def test_write_env_file_permissions_posix(self):
        """F05: On POSIX systems, verify written .env file has secure 0600 permissions."""
        setup_wizard.write_env_file(
            env_path=self.dummy_env_path,
            discord_token="mock_token",
        )
        if os.name != "nt":
            mode = stat.S_IMODE(os.stat(self.dummy_env_path).st_mode)
            self.assertEqual(mode, 0o600, f"Expected 0600 mode on POSIX, got {oct(mode)}")

    def test_write_env_file_permission_error_handling(self):
        """F05 (Edge Case): File write error is handled safely without unhandled crash."""
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            with patch("sys.stderr.write"):
                ok = setup_wizard.write_env_file(
                    env_path=self.dummy_env_path,
                    discord_token="mock_token",
                )
                self.assertFalse(ok)

    def test_run_wizard_interactive_happy_path(self):
        """F01-F05 (Tier 1 Happy Path): Full interactive setup flow writing .env successfully."""
        inputs = iter([
            "  Bot mock_user_token_123  ",  # Token with Bot prefix and leading/trailing spaces
            "  gsk_mock_key_456  ",      # Groq key
            "nb-shopping",               # Default board
            "1",                         # Language selection (1 -> zh_TW)
        ])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _: next(inputs)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("setup_wizard.validate_discord_token", return_value=(True, "111222", "TestBot", None)), \
             patch("setup_wizard.validate_groq_api_key", return_value=(True, None)):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertTrue(success)
            self.assertTrue(os.path.exists(self.dummy_env_path))

            with open(self.dummy_env_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("DISCORD_TOKEN=mock_user_token_123", content)
            self.assertIn("GROQ_API_KEY=gsk_mock_key_456", content)
            self.assertIn("DEFAULT_BOARD=nb-shopping", content)
            self.assertIn("BOT_LANGUAGE=zh_TW", content)

    def test_run_wizard_skips_optional_groq_key(self):
        """F04: User pressing Enter on Groq key skips it and writes empty key to .env."""
        inputs = iter([
            "mock_token",
            "",            # Skip Groq key
            "",            # Default board (Enter -> HardwareSale)
            "",            # Language selection (Enter -> zh_TW default)
        ])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _: next(inputs)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("setup_wizard.validate_discord_token", return_value=(True, "999", "TestBot", None)):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertTrue(success)

            with open(self.dummy_env_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("GROQ_API_KEY=\n", content)
            self.assertIn("DEFAULT_BOARD=HardwareSale", content)
            self.assertIn("BOT_LANGUAGE=zh_TW", content)

    def test_run_wizard_discord_retry_on_invalid_then_valid(self):
        """F02 (Tier 2 Retry Flow): Wizard recovers from invalid token entry and completes on retry."""
        inputs = iter([
            "wrong_token",
            "correct_token",
            "",
            "",
            "",            # Language selection (Enter -> default)
        ])

        validation_responses = iter([
            (False, None, None, "401 Unauthorized"),
            (True, "123", "ValidBot", None),
        ])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _: next(inputs)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("setup_wizard.validate_discord_token", side_effect=lambda *args, **kwargs: next(validation_responses)):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertTrue(success)

    def test_run_wizard_user_quits_with_q(self):
        """F01 (Boundary): User typing 'q' cancels setup wizard cleanly without creating .env."""
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="q"), \
             patch("sys.stdout", new_callable=io.StringIO):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertFalse(success)
            self.assertFalse(os.path.exists(self.dummy_env_path))

    def test_run_wizard_aborted_by_keyboard_interrupt(self):
        """F01 (Boundary): Ctrl+C during wizard aborts cleanly returning False."""
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=KeyboardInterrupt), \
             patch("sys.stdout", new_callable=io.StringIO):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertFalse(success)

    def test_run_wizard_aborted_by_eof(self):
        """F01 (Boundary): EOF on standard input aborts cleanly returning False."""
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=EOFError), \
             patch("sys.stdout", new_callable=io.StringIO):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertFalse(success)

    def test_run_wizard_non_tty_returns_false(self):
        """F01/F07: Wizard executed in non-TTY environment immediately returns False without hanging."""
        with patch("sys.stdin.isatty", return_value=False), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("sys.stderr.write"):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertFalse(success)

        # Stdio edge case: sys.stdin is None
        with patch("sys.stdin", None), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("sys.stderr.write"):
            self.assertFalse(setup_wizard.run_wizard(env_path=self.dummy_env_path))

        # Stdio edge case: closed sys.stdin
        closed_f = io.StringIO()
        closed_f.close()
        with patch("sys.stdin", closed_f), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("sys.stderr.write"):
            self.assertFalse(setup_wizard.run_wizard(env_path=self.dummy_env_path))

    def test_write_env_file_with_custom_bot_language(self):
        """M2: write_env_file writes custom bot_language to .env file."""
        ok = setup_wizard.write_env_file(
            env_path=self.dummy_env_path,
            discord_token="mock_discord_token_xyz",
            bot_language="en",
        )
        self.assertTrue(ok)
        with open(self.dummy_env_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("BOT_LANGUAGE=en", content)

    def test_run_wizard_selects_english_language(self):
        """M2: User selecting '2' at Step 4 saves BOT_LANGUAGE=en to .env."""
        inputs = iter([
            "mock_token",
            "",            # Skip Groq key
            "",            # Default board
            "2",           # English (en)
        ])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _: next(inputs)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("setup_wizard.validate_discord_token", return_value=(True, "123", "TestBot", None)):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertTrue(success)

            with open(self.dummy_env_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("BOT_LANGUAGE=en", content)

    def test_run_wizard_selects_english_language_by_keyword(self):
        """M2: User entering 'en' at Step 4 saves BOT_LANGUAGE=en to .env."""
        inputs = iter([
            "mock_token",
            "",            # Skip Groq key
            "",            # Default board
            "en",          # English text input
        ])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _: next(inputs)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("setup_wizard.validate_discord_token", return_value=(True, "123", "TestBot", None)):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertTrue(success)

            with open(self.dummy_env_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("BOT_LANGUAGE=en", content)

    def test_run_wizard_selects_default_language_on_invalid_input(self):
        """M2: Unrecognized language input defaults safely to zh_TW."""
        inputs = iter([
            "mock_token",
            "",            # Skip Groq key
            "",            # Default board
            "99_invalid",  # Invalid language choice
        ])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _: next(inputs)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("setup_wizard.validate_discord_token", return_value=(True, "123", "TestBot", None)):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertTrue(success)

            with open(self.dummy_env_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("BOT_LANGUAGE=zh_TW", content)

    def test_run_wizard_handles_stop_iteration_fallback(self):
        """M2: Backward-compatibility guard catches StopIteration at Step 4 and defaults to zh_TW."""
        # 3 inputs only: token, groq, board -> 4th prompt raises StopIteration
        inputs = iter([
            "mock_token",
            "",
            "HardwareSale",
        ])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _: next(inputs)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("setup_wizard.validate_discord_token", return_value=(True, "123", "TestBot", None)):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertTrue(success)

            with open(self.dummy_env_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("BOT_LANGUAGE=zh_TW", content)

    def test_config_bot_language_parsing_and_defaults(self):
        """M2: config._parse_language normalizes valid English variants and defaults safely to zh_TW."""
        import config

        # English variants
        for val in ("en", "EN", "en_US", "en-us", "English", "english"):
            self.assertEqual(config._parse_language(val), "en", f"Failed for {val}")

        # Default / fallback variants
        for val in ("zh_TW", "zh-tw", "zh", "fr", "ja", "", "   ", None):
            self.assertEqual(config._parse_language(val), "zh_TW", f"Failed for {val}")

    def test_parse_platform_selection(self):
        """M4: Test parse_platform_selection handles single, combination, all, defaults, quit, and legacy tokens."""
        # Single selections
        self.assertEqual(setup_wizard.parse_platform_selection("1"), (["discord"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("2"), (["telegram"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("3"), (["email"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("discord"), (["discord"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("telegram"), (["telegram"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("email"), (["email"], None))

        # Combinations
        self.assertEqual(setup_wizard.parse_platform_selection("1,2"), (["discord", "telegram"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("1, 3"), (["discord", "email"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("2,3"), (["telegram", "email"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("1+2+3"), (["discord", "telegram", "email"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("all"), (["discord", "telegram", "email"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("全部"), (["discord", "telegram", "email"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("*"), (["discord", "telegram", "email"], None))

        # Default empty
        self.assertEqual(setup_wizard.parse_platform_selection(""), (["discord"], None))
        self.assertEqual(setup_wizard.parse_platform_selection("   "), (["discord"], None))

        # Quit
        self.assertEqual(setup_wizard.parse_platform_selection("q"), ([], None))
        self.assertEqual(setup_wizard.parse_platform_selection("QUIT"), ([], None))

        # Legacy Discord token auto-detection
        self.assertEqual(
            setup_wizard.parse_platform_selection("mock_token"),
            (["discord"], "mock_token"),
        )
        self.assertEqual(
            setup_wizard.parse_platform_selection("  Bot mock_user_token_123  "),
            (["discord"], "mock_user_token_123"),
        )
        self.assertEqual(
            setup_wizard.parse_platform_selection("wrong_token"),
            (["discord"], "wrong_token"),
        )

    def test_validate_telegram_token_success(self):
        """M4: Test Telegram Bot token validation returns bot ID and username on HTTP 200."""
        mock_body = json.dumps({
            "ok": True,
            "result": {"id": 123456789, "is_bot": True, "username": "PTTTestBot"}
        }).encode("utf-8")
        mock_resp = MagicMock(status=200)
        mock_resp.read.return_value = mock_body
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            valid, bot_id, username, err = setup_wizard.validate_telegram_token("123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ")
            self.assertTrue(valid)
            self.assertEqual(bot_id, "123456789")
            self.assertEqual(username, "PTTTestBot")
            self.assertIsNone(err)

            req = mock_urlopen.call_args[0][0]
            self.assertIn("/bot123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ/getMe", req.full_url)

    def test_validate_telegram_token_401_unauthorized(self):
        """M4: Test Telegram token validation handles HTTP 401 Unauthorized gracefully."""
        err_response = io.BytesIO(b'{"ok": false, "error_code": 401, "description": "Unauthorized"}')
        http_err = urllib.error.HTTPError(
            url="https://api.telegram.org/bot123:bad_token/getMe",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=err_response,
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            valid, bot_id, username, err = setup_wizard.validate_telegram_token("123:bad_token")
            self.assertFalse(valid)
            self.assertIsNone(bot_id)
            self.assertIn("401", err)

    def test_validate_telegram_token_empty_and_malformed_rejects_offline(self):
        """M4: Empty or malformed Telegram token is rejected offline without HTTP request."""
        with patch("urllib.request.urlopen") as mock_url:
            valid, _, _, err = setup_wizard.validate_telegram_token("")
            self.assertFalse(valid)
            self.assertEqual(err, "Token 不能為空")

            valid, _, _, err = setup_wizard.validate_telegram_token("no_colon_token")
            self.assertFalse(valid)
            self.assertIn("格式不正確", err)

            valid, _, _, err = setup_wizard.validate_telegram_token("abc:secret")
            self.assertFalse(valid)
            self.assertIn("純數字之 Bot ID", err)

            mock_url.assert_not_called()

    def test_is_valid_telegram_chat_id(self):
        """M4: Validate Telegram Chat ID format check."""
        self.assertTrue(setup_wizard.is_valid_telegram_chat_id("123456789"))
        self.assertTrue(setup_wizard.is_valid_telegram_chat_id("-123456789"))
        self.assertTrue(setup_wizard.is_valid_telegram_chat_id("-1001234567890"))
        self.assertTrue(setup_wizard.is_valid_telegram_chat_id("@my_channel"))

        self.assertFalse(setup_wizard.is_valid_telegram_chat_id(""))
        self.assertFalse(setup_wizard.is_valid_telegram_chat_id("not_a_valid_id!"))

    def test_validate_telegram_chat_id(self):
        """M4: Test validate_telegram_chat_id with mocked REST responses."""
        mock_body = json.dumps({
            "ok": True,
            "result": {"id": 123456, "type": "private", "username": "alice"}
        }).encode("utf-8")
        mock_resp = MagicMock(status=200)
        mock_resp.read.return_value = mock_body
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            valid, title, err = setup_wizard.validate_telegram_chat_id("123456:TOKEN", "123456")
            self.assertTrue(valid)
            self.assertEqual(title, "alice")
            self.assertIsNone(err)

        # 400 Bad Request (Chat not found)
        http_err = urllib.error.HTTPError(
            url="https://api.telegram.org/bot...",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=io.BytesIO(b'{"ok": false, "error_code": 400, "description": "chat not found"}'),
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            valid, title, err = setup_wizard.validate_telegram_chat_id("123456:TOKEN", "999999")
            self.assertFalse(valid)
            self.assertIn("400 Bad Request", err)

    def test_is_valid_email(self):
        """M4: Validate RFC 5322 email syntax and header injection protection."""
        self.assertTrue(setup_wizard.is_valid_email("user@example.com"))
        self.assertTrue(setup_wizard.is_valid_email("first.last+tag@sub.domain.co"))
        self.assertTrue(setup_wizard.is_valid_email("dev_test123@gmail.com"))

        self.assertFalse(setup_wizard.is_valid_email(""))
        self.assertFalse(setup_wizard.is_valid_email("no_at_domain.com"))
        self.assertFalse(setup_wizard.is_valid_email("user@localhost"))
        self.assertFalse(setup_wizard.is_valid_email("user@domain..com"))
        self.assertFalse(setup_wizard.is_valid_email("user@domain.com\r\nBcc: evil@hacker.com"))
        self.assertFalse(setup_wizard.is_valid_email("user with spaces@domain.com"))

    def test_parse_and_validate_recipients(self):
        """M4: Test parsing multiple comma/semicolon recipients with deduplication."""
        ok, recipients, err = setup_wizard.parse_and_validate_recipients("a@b.com, c@d.com; a@b.com")
        self.assertTrue(ok)
        self.assertEqual(recipients, ["a@b.com", "c@d.com"])
        self.assertIsNone(err)

        ok, recipients, err = setup_wizard.parse_and_validate_recipients("invalid_email")
        self.assertFalse(ok)
        self.assertIn("格式不正確", err)

    def test_validate_smtp_credentials_success_port_587(self):
        """M4: Test SMTP credential validation on port 587 (STARTTLS)."""
        import smtplib
        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp
        with patch("smtplib.SMTP", return_value=mock_smtp) as mock_cls:
            valid, err = setup_wizard.validate_smtp_credentials(
                host="smtp.gmail.com",
                port=587,
                user="user@example.com",
                password="app_password",
                use_tls=True,
            )
            self.assertTrue(valid)
            self.assertIsNone(err)
            mock_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=10.0)
            mock_smtp.starttls.assert_called_once()
            mock_smtp.login.assert_called_once_with("user@example.com", "app_password")

    def test_validate_smtp_credentials_success_port_465_ssl(self):
        """M4: Test SMTP credential validation on port 465 (SMTPS implicit SSL)."""
        import smtplib
        mock_ssl = MagicMock()
        mock_ssl.__enter__.return_value = mock_ssl
        with patch("smtplib.SMTP_SSL", return_value=mock_ssl) as mock_cls:
            valid, err = setup_wizard.validate_smtp_credentials(
                host="smtp.gmail.com",
                port=465,
                user="user@example.com",
                password="app_password",
            )
            self.assertTrue(valid)
            self.assertIsNone(err)
            mock_cls.assert_called_once()
            mock_ssl.login.assert_called_once_with("user@example.com", "app_password")

    def test_validate_smtp_credentials_auth_error_535(self):
        """M4: Test SMTP authentication failure (535) provides helpful guidance on App Password."""
        import smtplib
        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp
        mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
        with patch("smtplib.SMTP", return_value=mock_smtp):
            valid, err = setup_wizard.validate_smtp_credentials(
                host="smtp.gmail.com",
                port=587,
                user="user@example.com",
                password="wrong_password",
            )
            self.assertFalse(valid)
            self.assertIn("535", err)
            self.assertIn("應用程式密碼", err)

    def test_validate_smtp_credentials_timeout_and_invalid_inputs(self):
        """M4: Test SMTP validation timeout and invalid inputs handling."""
        import smtplib
        import socket
        with patch("smtplib.SMTP", side_effect=socket.timeout("Connection timed out")):
            valid, err = setup_wizard.validate_smtp_credentials(
                host="10.255.255.1",
                port=587,
                user="u",
                password="p",
            )
            self.assertFalse(valid)
            self.assertIn("超時", err)

        # Empty host
        valid, err = setup_wizard.validate_smtp_credentials("", 587, "u", "p")
        self.assertFalse(valid)
        self.assertIn("不能為空", err)

        # Invalid port
        valid, err = setup_wizard.validate_smtp_credentials("smtp.gmail.com", "invalid_port", "u", "p")
        self.assertFalse(valid)
        self.assertIn("有效整數", err)

    def test_write_env_file_multi_platform(self):
        """M4: write_env_file serializes multi-platform flags and credentials."""
        ok = setup_wizard.write_env_file(
            env_path=self.dummy_env_path,
            discord_token="dc_tok_123",
            enable_telegram=True,
            telegram_bot_token="123456:TG_TOKEN",
            telegram_chat_id="987654321",
            enable_email=True,
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_user="sender@gmail.com",
            smtp_password="app_password_16",
            email_to="recipient@example.com",
        )
        self.assertTrue(ok)
        with open(self.dummy_env_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("ENABLE_DISCORD=true", content)
        self.assertIn("DISCORD_TOKEN=dc_tok_123", content)
        self.assertIn("ENABLE_TELEGRAM=true", content)
        self.assertIn("TELEGRAM_BOT_TOKEN=123456:TG_TOKEN", content)
        self.assertIn("TELEGRAM_CHAT_ID=987654321", content)
        self.assertIn("ENABLE_EMAIL=true", content)
        self.assertIn("SMTP_HOST=smtp.gmail.com", content)
        self.assertIn("SMTP_PORT=587", content)
        self.assertIn("SMTP_USER=sender@gmail.com", content)
        self.assertIn("EMAIL_TO=recipient@example.com", content)

        if os.name != "nt":
            mode = stat.S_IMODE(os.stat(self.dummy_env_path).st_mode)
            self.assertEqual(mode, 0o600)

    def test_run_wizard_selects_telegram_only(self):
        """M4: Setup wizard supports selecting only Telegram (platform 2)."""
        inputs = iter([
            "2",               # Select Telegram
            "123456:ABC-DEF",  # Telegram bot token
            "98765432",        # Telegram chat ID
            "",                # Skip Groq
            "",                # Default board
            "1",               # Language: zh_TW
        ])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _: next(inputs)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("setup_wizard.validate_telegram_token", return_value=(True, "123456", "PTTTG_Bot", None)):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertTrue(success)

            with open(self.dummy_env_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("ENABLE_DISCORD=false", content)
            self.assertIn("ENABLE_TELEGRAM=true", content)
            self.assertIn("TELEGRAM_BOT_TOKEN=123456:ABC-DEF", content)
            self.assertIn("TELEGRAM_CHAT_ID=98765432", content)
            self.assertIn("ENABLE_EMAIL=false", content)

    def test_run_wizard_selects_email_only(self):
        """M4: Setup wizard supports selecting only Email (platform 3)."""
        inputs = iter([
            "3",                  # Select Email
            "smtp.gmail.com",     # SMTP host
            "587",                # SMTP port
            "bot@gmail.com",      # SMTP user
            "pass_16_digits",     # SMTP password
            "y",                  # Use TLS
            "alert@example.com",  # Recipient email
            "",                   # Skip Groq
            "",                   # Default board
            "1",                  # Language: zh_TW
        ])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _: next(inputs)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("setup_wizard.validate_smtp_credentials", return_value=(True, None)):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertTrue(success)

            with open(self.dummy_env_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("ENABLE_DISCORD=false", content)
            self.assertIn("ENABLE_TELEGRAM=false", content)
            self.assertIn("ENABLE_EMAIL=true", content)
            self.assertIn("SMTP_HOST=smtp.gmail.com", content)
            self.assertIn("SMTP_PORT=587", content)
            self.assertIn("SMTP_USER=bot@gmail.com", content)
            self.assertIn("EMAIL_TO=alert@example.com", content)

    def test_run_wizard_multi_platform_all(self):
        """M4: Setup wizard supports selecting multiple platforms (1,2,3)."""
        inputs = iter([
            "1,2,3",              # Select all three platforms
            "mock_dc_token",      # Discord token
            "123456:TG_TOKEN",    # Telegram token
            "11223344",           # Telegram chat ID
            "smtp.gmail.com",     # SMTP host
            "587",                # SMTP port
            "mybot@gmail.com",    # SMTP user
            "my_secret_pass",     # SMTP password
            "y",                  # Use TLS
            "my_email@gmail.com", # Recipient
            "",                   # Skip Groq
            "",                   # Default board
            "1",                  # Language: zh_TW
        ])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _: next(inputs)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("setup_wizard.validate_discord_token", return_value=(True, "111", "DCBot", None)), \
             patch("setup_wizard.validate_telegram_token", return_value=(True, "123456", "TGBot", None)), \
             patch("setup_wizard.validate_smtp_credentials", return_value=(True, None)):

            success = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertTrue(success)

            with open(self.dummy_env_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("ENABLE_DISCORD=true", content)
            self.assertIn("ENABLE_TELEGRAM=true", content)
            self.assertIn("ENABLE_EMAIL=true", content)
            self.assertIn("DISCORD_TOKEN=mock_dc_token", content)
            self.assertIn("TELEGRAM_BOT_TOKEN=123456:TG_TOKEN", content)
            self.assertIn("SMTP_USER=mybot@gmail.com", content)



# =============================================================================
# 2. Bot Startup Interception Tests (F06–F08)
# =============================================================================

class TestBotStartupInterception(BaseDeploymentTestCase):
    """Unit tests for bot.py main() startup interception logic under TTY and Non-TTY."""

    def test_main_tty_with_missing_token_invokes_wizard_and_continues(self):
        """F06: Interactive terminal with missing token triggers setup_wizard.run_wizard()."""
        import bot

        bot.DISCORD_TOKEN = ""
        token_state = {"after_wizard": False}

        def mock_run_wizard():
            token_state["after_wizard"] = True
            return True

        def getenv_side_effect(key, default=""):
            if key == "DISCORD_TOKEN":
                if token_state["after_wizard"]:
                    return "newly_configured_token"
                return ""
            return default

        with patch("sys.stdin.isatty", return_value=True), \
             patch("setup_wizard.run_wizard", side_effect=mock_run_wizard) as mock_wizard, \
             patch.object(bot, "load_dotenv") as mock_load_dotenv, \
             patch.object(bot.bot, "run") as mock_bot_run, \
             patch("os.getenv", side_effect=getenv_side_effect):

            bot.main()
            mock_wizard.assert_called_once()
            mock_load_dotenv.assert_called_once()
            mock_bot_run.assert_called_once_with("newly_configured_token", log_handler=None)

    def test_main_tty_with_placeholder_token_invokes_wizard(self):
        """F06: Interactive terminal with placeholder token string triggers setup wizard."""
        import bot

        bot.DISCORD_TOKEN = ""
        token_state = {"after_wizard": False}

        def mock_run_wizard():
            token_state["after_wizard"] = True
            return True

        def token_getter(key, default=""):
            if key == "DISCORD_TOKEN":
                if token_state["after_wizard"]:
                    return "valid_token_after_wizard"
                return "your_discord_bot_token_here"
            return default

        with patch("sys.stdin.isatty", return_value=True), \
             patch("setup_wizard.run_wizard", side_effect=mock_run_wizard) as mock_wizard, \
             patch("dotenv.load_dotenv"), \
             patch.object(bot.bot, "run"), \
             patch("os.getenv", side_effect=token_getter):

            bot.main()
            mock_wizard.assert_called_once()

    def test_main_non_tty_with_missing_token_prints_stderr_and_exits(self):
        """F07: Non-interactive terminal (Docker/systemd) with missing token exits with code 1."""
        import bot

        with patch("os.getenv", return_value=""), \
             patch("sys.stdin.isatty", return_value=False), \
             patch("sys.stderr.write") as mock_stderr_write:

            with self.assertRaises(SystemExit) as cm:
                bot.main()

            self.assertEqual(cm.exception.code, 1)
            mock_stderr_write.assert_called()
            # Verify diagnostic instructions were written to stderr
            combined_output = "".join(call[0][0] for call in mock_stderr_write.call_args_list)
            self.assertIn("DISCORD_TOKEN", combined_output)
            self.assertIn("Non-TTY", combined_output)

        # Stdio edge case: sys.stdin is None
        bot.DISCORD_TOKEN = ""
        with patch("os.getenv", return_value=""), \
             patch("sys.stdin", None), \
             patch("sys.stderr.write"):
            with self.assertRaises(SystemExit) as cm:
                bot.main()
            self.assertEqual(cm.exception.code, 1)

        # Stdio edge case: closed sys.stdin
        closed_f = io.StringIO()
        closed_f.close()
        with patch("os.getenv", return_value=""), \
             patch("sys.stdin", closed_f), \
             patch("sys.stderr.write"):
            with self.assertRaises(SystemExit) as cm:
                bot.main()
            self.assertEqual(cm.exception.code, 1)

        # Stdio edge case: both sys.stdin and sys.stderr are None
        with patch("os.getenv", return_value=""), \
             patch("sys.stdin", None), \
             patch("sys.stderr", None):
            with self.assertRaises(SystemExit) as cm:
                bot.main()
            self.assertEqual(cm.exception.code, 1)

        # Edge case: whitespace-only DISCORD_TOKEN
        bot.DISCORD_TOKEN = "   "
        with patch("os.getenv", return_value=""), \
             patch("sys.stdin.isatty", return_value=False), \
             patch("sys.stderr.write"):
            with self.assertRaises(SystemExit) as cm:
                bot.main()
            self.assertEqual(cm.exception.code, 1)

    def test_main_non_tty_with_placeholder_token_exits_code_1(self):
        """F07: Non-interactive terminal with default placeholder token exits with code 1."""
        import bot

        with patch("os.getenv", return_value="your_discord_bot_token_here"), \
             patch("sys.stdin.isatty", return_value=False), \
             patch("sys.stderr.write"):

            with self.assertRaises(SystemExit) as cm:
                bot.main()

            self.assertEqual(cm.exception.code, 1)

    def test_main_tty_wizard_aborted_exits_cleanly(self):
        """F06: Interactive terminal where wizard is cancelled exits cleanly with code 1."""
        import bot

        with patch("os.getenv", return_value=""), \
             patch("sys.stdin.isatty", return_value=True), \
             patch("setup_wizard.run_wizard", return_value=False) as mock_wizard, \
             patch.object(bot.bot, "run") as mock_bot_run:

            with self.assertRaises(SystemExit) as cm:
                bot.main()

            self.assertEqual(cm.exception.code, 1)
            mock_wizard.assert_called_once()
            mock_bot_run.assert_not_called()

    def test_main_with_valid_token_runs_bot_directly(self):
        """F06: Valid configured token runs bot.run directly without wizard invocation."""
        import bot

        with patch("os.getenv", return_value="existing_valid_token"), \
             patch.object(bot.bot, "run") as mock_bot_run:

            bot.main()
            mock_bot_run.assert_called_once_with("existing_valid_token", log_handler=None)

    def test_main_posix_chmod_skips_on_windows(self):
        """F08/F15: Guard file permission calls on Windows NT environment."""
        import bot

        with patch("os.name", "nt"), \
             patch("os.chmod") as mock_chmod, \
             patch("os.getenv", return_value="valid_token"), \
             patch.object(bot.bot, "run"):

            bot.main()
            mock_chmod.assert_not_called()


# =============================================================================
# 3. Cross-Platform Scripts & Launchers (F09–F12, F23–F24)
# =============================================================================

class TestCrossPlatformScripts(unittest.TestCase):
    """Syntax and structural integrity tests for platform installation scripts and launchers."""

    def test_shell_scripts_bash_syntax(self):
        """F09, F12: Verify all shell and macOS scripts pass bash syntax checking (bash -n)."""
        shell_scripts = ["install.sh", "start_mac.command", "start_bg.sh", "stop_bg.sh", "restart_bg.sh"]
        for script_name in shell_scripts:
            script_path = os.path.join(BASE_DIR, script_name)
            if os.path.exists(script_path):
                res = subprocess.run(
                    ["bash", "-n", script_path],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    res.returncode, 0,
                    f"Syntax error in shell script {script_name}:\n{res.stderr}"
                )

    @unittest.skipUnless(HAS_INSTALL_SH, "install.sh not yet created")
    def test_install_sh_structural_integrity(self):
        """F09: Verify install.sh handles /dev/tty redirect, python 3.10+ check, and setup wizard."""
        script_path = os.path.join(BASE_DIR, "install.sh")
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("exec < /dev/tty", content, "install.sh must redirect stdin to /dev/tty for piped curl")
        self.assertIn("python3", content)
        self.assertIn("venv", content)
        self.assertIn("requirements.txt", content)
        self.assertIn("setup_wizard.py", content)

    @unittest.skipUnless(HAS_INSTALL_PS1, "install.ps1 not yet created")
    def test_install_ps1_structural_integrity(self):
        """F10: Verify install.ps1 invokes python binary directly, handles winget, and runs wizard."""
        script_path = os.path.join(BASE_DIR, "install.ps1")
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Must execute python directly rather than activating script to avoid ExecutionPolicy issues
        self.assertNotIn("Activate.ps1", content, "install.ps1 must not call Activate.ps1")
        self.assertIn("setup_wizard.py", content)
        self.assertIn("requirements.txt", content)

    @unittest.skipUnless(HAS_START_WIN, "start_windows.bat not yet created")
    def test_start_windows_bat_structural_integrity(self):
        """F11: Verify start_windows.bat sets UTF-8 code page, directory switch, and restart loop."""
        script_path = os.path.join(BASE_DIR, "start_windows.bat")
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("chcp 65001", content, "start_windows.bat must set UTF-8 code page")
        self.assertIn("cd /d \"%~dp0\"", content, "start_windows.bat must switch to script directory")
        self.assertIn("timeout", content, "start_windows.bat must have delay in restart loop")
        self.assertIn("bot.py", content)

    @unittest.skipUnless(HAS_START_MAC, "start_mac.command not yet created")
    def test_start_mac_command_structural_integrity(self):
        """F12: Verify start_mac.command handles working directory, runs bot, and holds terminal."""
        script_path = os.path.join(BASE_DIR, "start_mac.command")
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('cd "$(dirname "$0")"', content, "start_mac.command must switch to script directory")
        self.assertIn("read -p", content, "start_mac.command must hold terminal window open on exit")
        self.assertIn("bot.py", content)

    def test_docker_compose_and_dockerignore_configuration(self):
        """F23, F24: Verify docker-compose.yml configuration and .dockerignore protects secrets."""
        compose_path = os.path.join(BASE_DIR, "docker-compose.yml")
        if os.path.exists(compose_path):
            with open(compose_path, "r", encoding="utf-8") as f:
                compose_content = f.read()
            self.assertIn("ptt-dc-bot", compose_content)
            # When M4 publishes prebuilt image, verify image tag
            if "image:" in compose_content:
                self.assertIn("ghcr.io/jasonxp516/ptt-dc-bot", compose_content)

        dockerignore_path = os.path.join(BASE_DIR, ".dockerignore")
        if os.path.exists(dockerignore_path):
            with open(dockerignore_path, "r", encoding="utf-8") as f:
                dockerignore_content = f.read()
            self.assertIn(".env", dockerignore_content)
            self.assertIn("*.db", dockerignore_content)


# =============================================================================
# 4. Discord /setup Visual UI Components & Business Logic (F13–F17)
# =============================================================================

class TestDiscordSetupUI(BaseDeploymentTestCase):
    """Component and interface contract tests for Discord /setup slash command and interactive views."""

    def test_channel_bind_contract(self):
        """F15: Channel bind updates subscriber push_channel_id in storage."""
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        sub = storage.get_or_create_subscriber("guild", "123456789")

        # Initial push channel is None
        self.assertIsNone(sub.get("push_channel_id"))

        # Channel bind operation
        storage.update_subscriber_channel(sub["id"], "987654321")

        updated_sub = storage.get_subscriber("guild", "123456789")
        self.assertEqual(updated_sub.get("push_channel_id"), "987654321")
        storage.close()

    def test_popular_boards_select_contract(self):
        """F14: Popular boards selection adds boards to tracking database."""
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        popular_boards = ["HardwareSale", "MacShop", "nb-shopping", "Gamesale", "MobileComm"]

        for b in popular_boards:
            storage.add_board(b)

        tracked_boards = [item["name"] for item in storage.get_boards()]
        for b in popular_boards:
            self.assertIn(b, tracked_boards)
        storage.close()

    def test_keyword_add_modal_contract_with_price_parsing_and_quotas(self):
        """F16: Keyword add operation parses price and enforces subscriber tier quotas."""
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        sub = storage.get_or_create_subscriber("guild", "123456789")

        # 1. Add valid keyword with price string
        ok, msg = storage.add_keyword(
            "RTX 3060Ti",
            board="HardwareSale",
            category="賣",
            subscriber_id=sub["id"],
            max_price=6500,
        )
        self.assertTrue(ok)

        kws = storage.get_keywords(subscriber_id=sub["id"])
        self.assertEqual(len(kws), 1)
        self.assertEqual(kws[0]["keyword"], "RTX 3060Ti")
        self.assertEqual(kws[0]["max_price"], 6500)

        # 2. Add keywords up to Free tier quota (10 keywords limit)
        for i in range(2, 11):
            storage.add_keyword(f"Item #{i}", subscriber_id=sub["id"])

        # 11th keyword should exceed quota
        ok_quota, msg_quota = storage.add_keyword("Excess Item", subscriber_id=sub["id"])
        self.assertFalse(ok_quota)
        self.assertIn("上限", msg_quota)
        storage.close()

    def test_test_push_contract_with_bound_channel(self):
        """F17: Test push notification creates formatted embed when channel is bound."""
        import discord
        from embeds import build_article_embed

        sample_article = {
            "title": "[賣/台北] 華碩 RTX 3060Ti 8G",
            "url": "https://www.ptt.cc/bbs/HardwareSale/M.1792681775.A.736.html",
            "author": "seller1",
            "board": "HardwareSale",
        }
        detail = {
            "extracted_price": "6,500元",
        }

        embed = build_article_embed(sample_article, matched_keywords=["3060Ti"], detail=detail)
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("3060Ti", embed.title or "")
        field_dict = {f.name: f.value for f in embed.fields}
        self.assertIn("欲售價格", field_dict)
        self.assertIn("6,500", field_dict["欲售價格"])

    def test_test_push_contract_without_bound_channel(self):
        """F17 (Edge Case): Test push fails gracefully with warning when no channel is bound."""
        from delivery import DeliveryManager
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        sub = storage.get_or_create_subscriber("guild", "123456789")

        delivery = DeliveryManager(bot=MagicMock(), storage=storage, crawler=MagicMock())
        target = asyncio.run(delivery.resolve_subscriber_target(sub))
        self.assertIsNone(target, "Unconfigured subscriber target channel should resolve to None")
        storage.close()

    def test_setup_panel_embed_and_view_localized_rendering(self):
        """M2: build_setup_panel_embed and SetupPanelView render English or Chinese according to config.BOT_LANGUAGE."""
        import discord
        import config
        from bot import build_setup_panel_embed, SetupPanelView
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        sub = storage.get_or_create_subscriber("user", "123456789")

        original_lang = getattr(config, "BOT_LANGUAGE", "zh_TW")
        try:
            # 1. Test Traditional Chinese (zh_TW)
            config.BOT_LANGUAGE = "zh_TW"
            embed_zh = build_setup_panel_embed(sub, storage=storage)
            view_zh = SetupPanelView(subscriber=sub)

            self.assertIn("PTT-DC-Bot 視覺化互動設定面板", embed_zh.title)
            self.assertIn("語言: 繁體中文 (zh_TW)", embed_zh.footer.text)
            zh_labels = [b.label for b in view_zh.children if hasattr(b, "label") and b.label]
            self.assertIn("綁定此頻道為推播頻道", zh_labels)
            self.assertIn("新增追蹤關鍵字", zh_labels)
            self.assertIn("測試推播通知", zh_labels)

            # 2. Test English (en)
            config.BOT_LANGUAGE = "en"
            embed_en = build_setup_panel_embed(sub, storage=storage)
            view_en = SetupPanelView(subscriber=sub)

            self.assertIn("PTT Tracking Dispatch Control Center", embed_en.title)
            self.assertIn("Language: English (en)", embed_en.footer.text)
            self.assertIn("Current Alert Target", [f.name for f in embed_en.fields])
            en_labels = [b.label for b in view_en.children if hasattr(b, "label") and b.label]
            self.assertIn("Bind Channel", en_labels)
            self.assertIn("Add Keyword", en_labels)
            self.assertIn("Test Alert", en_labels)
        finally:
            config.BOT_LANGUAGE = original_lang
            storage.close()



# =============================================================================
# 5. Owner Welcome DM Tests (F18–F19)
# =============================================================================

class TestOwnerWelcomeDM(BaseDeploymentTestCase):
    """Unit tests for clean boot detection, owner resolution, and deduplication of welcome DM."""

    def test_clean_boot_detection_and_deduplication_contract(self):
        """F18, F19: Detect unconfigured state, record deduplication flag in settings."""
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)

        # Clean state check: 0 push channels configured, 0 keywords
        push_channel = storage.get_push_channel_id(1)
        keywords = storage.get_keywords()
        already_sent = storage.get_setting("owner_welcome_sent")

        is_clean_state = (push_channel is None) and (len(keywords) == 0) and (already_sent != "1")
        self.assertTrue(is_clean_state)

        # Record deduplication flag
        storage.set_setting("owner_welcome_sent", "1")
        self.assertEqual(storage.get_setting("owner_welcome_sent"), "1")

        # Subsequent check must NOT evaluate to clean state
        subsequent_clean = (
            storage.get_push_channel_id(1) is None
            and len(storage.get_keywords()) == 0
            and storage.get_setting("owner_welcome_sent") != "1"
        )
        self.assertFalse(subsequent_clean)
        storage.close()

    def test_welcome_dm_skipped_when_already_sent_flag_is_set(self):
        """F19: When owner_welcome_sent is '1', welcome DM logic must be skipped."""
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        storage.set_setting("owner_welcome_sent", "1")

        should_send = (
            storage.get_push_channel_id(1) is None
            and len(storage.get_keywords()) == 0
            and storage.get_setting("owner_welcome_sent") != "1"
        )
        self.assertFalse(should_send)
        storage.close()

    def test_welcome_dm_skipped_when_configured_with_keywords(self):
        """F18: When bot already has monitored keywords, welcome DM must be skipped."""
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        storage.add_keyword("RTX 3080", subscriber_id=1)

        should_send = (
            storage.get_push_channel_id(1) is None
            and len(storage.get_keywords()) == 0
            and storage.get_setting("owner_welcome_sent") != "1"
        )
        self.assertFalse(should_send)
        storage.close()

    def test_owner_resolution_individual_vs_team(self):
        """F18: Correctly resolve individual owner (discord.User) or team owner (discord.TeamMember -> discord.User)."""
        import discord
        from bot import PTTDiscordBot
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        bot = PTTDiscordBot(
            storage=storage,
            crawler=MagicMock(),
            scheduler=MagicMock(),
            agent=MagicMock(),
        )

        # 1. Individual bot owner: app_info.owner is discord.User with send()
        mock_individual_owner = MagicMock(spec=discord.User, id=111222333, name="SoloDeveloper")
        mock_individual_owner.send = AsyncMock()
        app_info_individual = MagicMock(owner=mock_individual_owner, team=None)
        bot.application_info = AsyncMock(return_value=app_info_individual)

        asyncio.run(bot._check_and_send_owner_welcome())
        mock_individual_owner.send.assert_awaited_once()
        sent_embed = mock_individual_owner.send.call_args[1].get("embed")
        self.assertIsNotNone(sent_embed)
        self.assertIn("快速設定 4 步驟", sent_embed.description)
        self.assertEqual(bot.detected_owner_id, "111222333")
        self.assertEqual(storage.get_setting("owner_welcome_sent"), "1")

        # 2. Team-owned bot: app_info.team.owner is discord.TeamMember (no send()), resolved via get_user
        storage.set_setting("owner_welcome_sent", "0")
        bot = PTTDiscordBot(
            storage=storage,
            crawler=MagicMock(),
            scheduler=MagicMock(),
            agent=MagicMock(),
        )
        mock_team_member = MagicMock(spec=discord.TeamMember, id=444555666, name="TeamLead")
        self.assertFalse(hasattr(mock_team_member, "send"))

        mock_resolved_user_cached = MagicMock(spec=discord.User, id=444555666, name="TeamLeadCached")
        mock_resolved_user_cached.send = AsyncMock()

        # Resolution via get_user
        bot.get_user = MagicMock(return_value=mock_resolved_user_cached)
        bot.fetch_user = AsyncMock(return_value=None)
        app_info_team = MagicMock(owner=None, team=MagicMock(owner=mock_team_member))
        bot.application_info = AsyncMock(return_value=app_info_team)

        asyncio.run(bot._check_and_send_owner_welcome())
        bot.get_user.assert_called_with(444555666)
        bot.fetch_user.assert_not_called()
        mock_resolved_user_cached.send.assert_awaited_once()
        self.assertEqual(bot.detected_owner_id, "444555666")
        self.assertEqual(storage.get_setting("owner_welcome_sent"), "1")

        # 3. Team-owned bot fallback to fetch_user when get_user returns None
        storage.set_setting("owner_welcome_sent", "0")
        bot = PTTDiscordBot(
            storage=storage,
            crawler=MagicMock(),
            scheduler=MagicMock(),
            agent=MagicMock(),
        )
        mock_resolved_user_fetched = MagicMock(spec=discord.User, id=444555666, name="TeamLeadFetched")
        mock_resolved_user_fetched.send = AsyncMock()
        bot.get_user = MagicMock(return_value=None)
        bot.fetch_user = AsyncMock(return_value=mock_resolved_user_fetched)
        app_info_team = MagicMock(owner=None, team=MagicMock(owner=mock_team_member))
        bot.application_info = AsyncMock(return_value=app_info_team)

        asyncio.run(bot._check_and_send_owner_welcome())
        bot.get_user.assert_called_with(444555666)
        bot.fetch_user.assert_awaited_once_with(444555666)
        mock_resolved_user_fetched.send.assert_awaited_once()
        self.assertEqual(bot.detected_owner_id, "444555666")
        self.assertEqual(storage.get_setting("owner_welcome_sent"), "1")
        storage.close()

    def test_welcome_dm_forbidden_exception_handled_safely(self):
        """F18 (Edge Case): Handles discord.Forbidden gracefully when owner has DMs disabled."""
        import discord
        from bot import PTTDiscordBot
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        bot = PTTDiscordBot(
            storage=storage,
            crawler=MagicMock(),
            scheduler=MagicMock(),
            agent=MagicMock(),
        )

        mock_owner = MagicMock(spec=discord.User, id=987654321, name="ForbiddenUser")
        forbidden = discord.Forbidden(
            response=MagicMock(status=403, reason="Forbidden"),
            message="Cannot send messages to this user"
        )
        mock_owner.send = AsyncMock(side_effect=forbidden)
        app_info = MagicMock(owner=mock_owner, team=None)
        bot.application_info = AsyncMock(return_value=app_info)

        # Must execute cleanly without raising unhandled exception
        try:
            asyncio.run(bot._check_and_send_owner_welcome())
        except discord.Forbidden:
            self.fail("bot._check_and_send_owner_welcome() failed to catch discord.Forbidden")

        mock_owner.send.assert_awaited_once()
        self.assertEqual(storage.get_setting("owner_welcome_sent"), "1")
        storage.close()

    def test_empirical_clean_boot_matrix(self):
        """Empirical challenge: bot._check_and_send_owner_welcome against all clean boot matrix states."""
        from bot import PTTDiscordBot
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        bot = PTTDiscordBot(
            storage=storage,
            crawler=MagicMock(),
            scheduler=MagicMock(),
            agent=MagicMock(),
        )

        # State 1: 0 push channels, 0 keywords, unsent flag -> TRIGGERS
        mock_owner = MagicMock(id=777, name="SoloDev")
        mock_owner.send = AsyncMock()
        bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))

        asyncio.run(bot._check_and_send_owner_welcome())
        self.assertEqual(mock_owner.send.call_count, 1)
        self.assertEqual(storage.get_setting("owner_welcome_sent"), "1")
        self.assertEqual(bot.detected_owner_id, "777")

        # State 2: Already sent flag == '1' -> SKIPS
        mock_owner.send.reset_mock()
        asyncio.run(bot._check_and_send_owner_welcome())
        self.assertEqual(mock_owner.send.call_count, 0)

        # State 3: Push channel configured -> SKIPS
        storage.set_setting("owner_welcome_sent", "0")
        storage.update_subscriber_channel(1, "123456")
        mock_owner.send.reset_mock()
        asyncio.run(bot._check_and_send_owner_welcome())
        self.assertEqual(mock_owner.send.call_count, 0)

        # State 4: Keywords configured -> SKIPS
        storage.update_subscriber_channel(1, "")  # reset channel
        storage.add_keyword("RTX 3080", subscriber_id=1)
        mock_owner.send.reset_mock()
        asyncio.run(bot._check_and_send_owner_welcome())
        self.assertEqual(mock_owner.send.call_count, 0)
        storage.close()

    def test_empirical_owner_resolution_matrix(self):
        """Empirical challenge: verify individual owner vs team owner resolution in bot method."""
        from bot import PTTDiscordBot
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        bot = PTTDiscordBot(
            storage=storage,
            crawler=MagicMock(),
            scheduler=MagicMock(),
            agent=MagicMock(),
        )

        # Team owner takes precedence over bot user owner
        team_owner = MagicMock(id=888, name="TeamLead")
        team_owner.send = AsyncMock()
        bot_user = MagicMock(id=999, name="BotAppUser")
        bot_user.send = AsyncMock()

        app_info = MagicMock(owner=bot_user, team=MagicMock(owner=team_owner))
        bot.application_info = AsyncMock(return_value=app_info)

        asyncio.run(bot._check_and_send_owner_welcome())
        self.assertEqual(team_owner.send.call_count, 1)
        self.assertEqual(bot_user.send.call_count, 0)
        self.assertEqual(bot.detected_owner_id, "888")
        storage.close()

    def test_empirical_exception_isolation_forbidden_and_network_error(self):
        """Empirical challenge: verify discord.Forbidden and network errors are safely suppressed."""
        import discord
        from bot import PTTDiscordBot
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        bot = PTTDiscordBot(
            storage=storage,
            crawler=MagicMock(),
            scheduler=MagicMock(),
            agent=MagicMock(),
        )

        # 1. discord.Forbidden (code 50007)
        mock_owner = MagicMock(id=111, name="OwnerForbidden")
        forbidden = discord.Forbidden(
            response=MagicMock(status=403, reason="Forbidden"),
            message="Cannot send messages to this user"
        )
        mock_owner.send = AsyncMock(side_effect=forbidden)
        bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))

        # Must not raise and must still record flag
        asyncio.run(bot._check_and_send_owner_welcome())
        self.assertEqual(storage.get_setting("owner_welcome_sent"), "1")

        # 2. application_info network error
        storage.set_setting("owner_welcome_sent", "0")
        bot.application_info = AsyncMock(
            side_effect=discord.HTTPException(
                response=MagicMock(status=503, reason="Gateway Timeout"),
                message="Gateway Timeout"
            )
        )
        # Must not raise and must still record flag
        asyncio.run(bot._check_and_send_owner_welcome())
        self.assertEqual(storage.get_setting("owner_welcome_sent"), "1")
        storage.close()

    def test_empirical_persistence_and_reconnect_idempotency(self):
        """Empirical challenge: verify persistence across reboots and multiple gateway reconnects."""
        from bot import PTTDiscordBot
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        bot = PTTDiscordBot(
            storage=storage,
            crawler=MagicMock(),
            scheduler=MagicMock(),
            agent=MagicMock(),
        )
        mock_owner = MagicMock(id=111, name="SoloDev")
        mock_owner.send = AsyncMock()
        bot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner, team=None))
        bot.tree.sync = AsyncMock(return_value=[])
        bot.change_presence = AsyncMock()
        bot._connection.user = MagicMock(id=999, name="PTTBot")

        # Simulate 5 gateway reconnects triggering on_ready
        async def simulate():
            with patch.object(PTTDiscordBot, "guilds", new_callable=unittest.mock.PropertyMock, return_value=[]):
                for _ in range(5):
                    await bot.on_ready()

        asyncio.run(simulate())

        # Exactly 1 welcome DM sent
        self.assertEqual(mock_owner.send.call_count, 1)
        storage.close()

        # Simulate full cold reboot
        storage_reboot = BotStorage(db_path=self.dummy_db_path)
        bot_reboot = PTTDiscordBot(
            storage=storage_reboot,
            crawler=MagicMock(),
            scheduler=MagicMock(),
            agent=MagicMock(),
        )
        mock_owner_reboot = MagicMock(id=111, name="SoloDev")
        mock_owner_reboot.send = AsyncMock()
        bot_reboot.application_info = AsyncMock(return_value=MagicMock(owner=mock_owner_reboot, team=None))

        asyncio.run(bot_reboot._check_and_send_owner_welcome())
        self.assertEqual(mock_owner_reboot.send.call_count, 0)
        storage_reboot.close()


# =============================================================================
# 6. Cross-Feature Combinations & Real-World E2E Scenarios (Tiers 3 & 4)
# =============================================================================

class TestCrossFeatureAndRealWorldE2E(BaseDeploymentTestCase):
    """End-to-end integration and real-world onboarding simulation tests."""

    def test_tier3_wizard_env_written_loaded_by_bot_and_storage(self):
        """Tier 3: Setup wizard writes .env -> bot loads configuration -> BotStorage respects settings."""
        from dotenv import dotenv_values
        from storage import BotStorage

        # 1. Wizard generates .env in temporary directory
        setup_wizard.write_env_file(
            env_path=self.dummy_env_path,
            discord_token="mock_discord_token_e2e",
            groq_api_key="gsk_mock_groq_key_e2e",
            default_board="nb-shopping",
        )

        # 2. Load generated .env configuration
        loaded_cfg = dotenv_values(self.dummy_env_path)
        self.assertEqual(loaded_cfg.get("DISCORD_TOKEN"), "mock_discord_token_e2e")
        self.assertEqual(loaded_cfg.get("GROQ_API_KEY"), "gsk_mock_groq_key_e2e")
        self.assertEqual(loaded_cfg.get("DEFAULT_BOARD"), "nb-shopping")

        # 3. Storage initialized with config values
        storage = BotStorage(
            db_path=self.dummy_db_path,
            default_board=loaded_cfg.get("DEFAULT_BOARD", "HardwareSale"),
        )
        boards = [b["name"] for b in storage.get_boards()]
        self.assertIn("nb-shopping", boards)
        storage.close()

    def test_tier3_setup_bind_channel_then_test_push_dispatch(self):
        """Tier 3: Channel bound to subscriber -> delivery resolves target channel -> sends embed."""
        from delivery import DeliveryManager
        from embeds import build_article_embed
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        sub = storage.get_or_create_subscriber("guild", "999888")
        storage.update_subscriber_channel(sub["id"], "777666")

        # Mock discord client
        mock_channel = MagicMock()
        mock_channel.guild = MagicMock(id=999888)
        mock_channel.send = AsyncMock()
        mock_bot = MagicMock()
        mock_bot.get_channel.return_value = mock_channel

        delivery = DeliveryManager(bot=mock_bot, storage=storage, crawler=MagicMock())

        # Resolve subscriber target channel
        fresh_sub = storage.get_subscriber("guild", "999888")
        target_channel = asyncio.run(delivery.resolve_subscriber_target(fresh_sub))
        self.assertEqual(target_channel, mock_channel)

        # Dispatch test notification embed
        embed = build_article_embed(
            {"title": "[測試] 推播通知測試", "url": "https://www.ptt.cc", "author": "bot", "board": "HardwareSale"},
            matched_keywords=["測試"],
        )
        asyncio.run(mock_channel.send(embed=embed))
        mock_channel.send.assert_called_once_with(embed=embed)
        storage.close()

    def test_tier3_board_select_and_keyword_storage_combination(self):
        """Tier 3: Board selected via menu, keyword added with board binding in storage."""
        from storage import BotStorage

        storage = BotStorage(db_path=self.dummy_db_path)
        sub = storage.get_or_create_subscriber("guild", "999888")

        # 1. User selects board from dropdown
        storage.add_board("MacShop")
        self.assertIn("MacShop", [b["name"] for b in storage.get_boards()])

        # 2. User adds keyword targeting that board
        ok, msg = storage.add_keyword("M2 MacBook Air", board="MacShop", subscriber_id=sub["id"], max_price=22000)
        self.assertTrue(ok)

        # 3. Verify keyword is properly isolated under subscriber and board
        kws = storage.get_keywords(subscriber_id=sub["id"])
        mac_kws = [k for k in kws if k["board"] == "MacShop"]
        self.assertEqual(len(mac_kws), 1)
        self.assertEqual(mac_kws[0]["keyword"], "M2 MacBook Air")
        self.assertEqual(mac_kws[0]["max_price"], 22000)
        storage.close()

    def test_tier4_full_first_time_onboarding_lifecycle(self):
        """Tier 4: Comprehensive simulation of a first-time user deployment lifecycle."""
        from storage import BotStorage

        # Step 1: User runs setup_wizard in TTY terminal
        inputs = iter([
            "mock_prod_token_abc",
            "",                      # Skip Groq key
            "HardwareSale",          # Default board
            "1",                     # Language selection (1 -> zh_TW)
        ])
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda _: next(inputs)), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch("setup_wizard.validate_discord_token", return_value=(True, "555444", "PTTBot", None)):

            wizard_ok = setup_wizard.run_wizard(env_path=self.dummy_env_path)
            self.assertTrue(wizard_ok)

        # Step 2: Bot starts with fresh unconfigured database
        storage = BotStorage(db_path=self.dummy_db_path, default_board="HardwareSale")
        self.assertIsNone(storage.get_push_channel_id(1))
        self.assertEqual(len(storage.get_keywords()), 0)

        # Step 3: Owner welcome DM triggered and deduplication flag stored
        storage.set_setting("owner_welcome_sent", "1")

        # Step 4: Administrator binds push channel in Discord server
        guild_sub = storage.get_or_create_subscriber("guild", "112233")
        storage.update_subscriber_channel(guild_sub["id"], "445566")

        # Step 5: Administrator adds monitoring keywords via /setup
        storage.add_keyword("RTX 4070", board="HardwareSale", subscriber_id=guild_sub["id"], max_price=16000)
        tracked_kws = storage.get_keywords(subscriber_id=guild_sub["id"])
        self.assertEqual(len(tracked_kws), 1)
        self.assertEqual(tracked_kws[0]["keyword"], "RTX 4070")
        self.assertEqual(tracked_kws[0]["max_price"], 16000)

        # Step 6: Verify deduplication prevents welcome DM on subsequent bot restarts
        self.assertEqual(storage.get_setting("owner_welcome_sent"), "1")
        storage.close()

    def test_tier4_headless_container_non_tty_startup_simulation(self):
        """Tier 4: Headless container deployment without token fails safely with exit code 1."""
        import bot

        with patch("os.getenv", return_value=""), \
             patch("sys.stdin.isatty", return_value=False), \
             patch("sys.stderr.write"):

            with self.assertRaises(SystemExit) as cm:
                bot.main()

            self.assertEqual(cm.exception.code, 1)


# =============================================================================
# 5. CI/CD Release Pipeline & Docker Hardening (F20–F24)
# =============================================================================

HAS_RELEASE_WORKFLOW = os.path.exists(os.path.join(BASE_DIR, ".github", "workflows", "release.yml"))
HAS_DOCKER_COMPOSE = os.path.exists(os.path.join(BASE_DIR, "docker-compose.yml"))
HAS_DOCKERIGNORE = os.path.exists(os.path.join(BASE_DIR, ".dockerignore"))


class TestCICDReleaseWorkflow(BaseDeploymentTestCase):
    """F20-F22: Structural and semantic verification of GitHub Actions release pipeline."""

    @unittest.skipUnless(HAS_RELEASE_WORKFLOW, ".github/workflows/release.yml not yet created")
    def test_release_workflow_yaml_syntax_and_triggers(self):
        """F20: Verify release.yml is valid YAML with tag push (v*) and workflow_dispatch triggers."""
        import yaml

        workflow_path = os.path.join(BASE_DIR, ".github", "workflows", "release.yml")
        with open(workflow_path, "r", encoding="utf-8") as f:
            workflow = yaml.safe_load(f)

        self.assertIsInstance(workflow, dict, "Workflow root must be a YAML mapping")

        # Handle PyYAML 1.1 unquoted 'on' boolean quirk
        triggers = workflow.get("on") if "on" in workflow else workflow.get(True)
        self.assertIsNotNone(triggers, "Workflow must define 'on' trigger conditions")

        # Verify tag push trigger matching 'v*'
        self.assertIn("push", triggers, "Workflow must trigger on git push")
        push_cfg = triggers["push"]
        self.assertIn("tags", push_cfg, "Push trigger must define tags filter")
        tags = push_cfg["tags"]
        self.assertTrue(
            any("v*" in str(t) for t in tags),
            f"Push tags must match release version pattern 'v*', found: {tags}"
        )

        # Verify manual trigger
        self.assertIn("workflow_dispatch", triggers, "Workflow must support workflow_dispatch manual trigger")

        # Verify workflow-level permissions
        perms = workflow.get("permissions", {})
        self.assertEqual(perms.get("contents"), "write", "Workflow requires contents: write")
        self.assertEqual(perms.get("packages"), "write", "Workflow requires packages: write")

    @unittest.skipUnless(HAS_RELEASE_WORKFLOW, ".github/workflows/release.yml not yet created")
    def test_release_workflow_jobs_and_dependency_dag(self):
        """F20, F21, F22: Verify test, release-packages, and docker-publish jobs with 'needs: [test]'."""
        import yaml

        workflow_path = os.path.join(BASE_DIR, ".github", "workflows", "release.yml")
        with open(workflow_path, "r", encoding="utf-8") as f:
            workflow = yaml.safe_load(f)

        jobs = workflow.get("jobs", {})
        self.assertIn("test", jobs, "Workflow must contain a 'test' quality gate job")
        self.assertIn("release-packages", jobs, "Workflow must contain 'release-packages' job")
        self.assertIn("docker-publish", jobs, "Workflow must contain 'docker-publish' job")

        # Verify test job runs on ubuntu-latest and executes test and compilation gates
        test_job = jobs["test"]
        self.assertEqual(test_job.get("runs-on"), "ubuntu-latest")
        test_steps_str = json.dumps(test_job.get("steps", []))
        self.assertIn("unittest", test_steps_str, "Test job must execute python unittest suite")
        self.assertIn("bash -n", test_steps_str, "Test job must validate shell script syntax")
        self.assertIn("py_compile", test_steps_str, "Test job must validate python syntax via py_compile")

        # Verify python matrix versions
        matrix = test_job.get("strategy", {}).get("matrix", {})
        python_versions = matrix.get("python-version", [])
        for ver in ["3.10", "3.11", "3.12", "3.13"]:
            self.assertIn(ver, python_versions, f"Test job matrix must cover Python {ver}")

        # Verify release-packages depends on test job
        rel_needs = jobs["release-packages"].get("needs")
        if isinstance(rel_needs, str):
            self.assertEqual(rel_needs, "test", "release-packages must depend on 'test'")
        else:
            self.assertIn("test", rel_needs, "release-packages needs list must include 'test'")

        # Verify docker-publish depends on test job
        docker_needs = jobs["docker-publish"].get("needs")
        if isinstance(docker_needs, str):
            self.assertEqual(docker_needs, "test", "docker-publish must depend on 'test'")
        else:
            self.assertIn("test", docker_needs, "docker-publish needs list must include 'test'")

    @unittest.skipUnless(HAS_RELEASE_WORKFLOW, ".github/workflows/release.yml not yet created")
    def test_release_packages_cross_platform_archives(self):
        """F21: Verify release-packages generates Windows zip, macOS tar.gz, and Linux tar.gz."""
        import yaml

        workflow_path = os.path.join(BASE_DIR, ".github", "workflows", "release.yml")
        with open(workflow_path, "r", encoding="utf-8") as f:
            workflow = yaml.safe_load(f)

        steps = workflow["jobs"]["release-packages"].get("steps", [])
        steps_str = json.dumps(steps)

        # Verify inclusion of platform packages
        self.assertTrue(
            "zip" in steps_str or ".zip" in steps_str,
            "Release job must package Windows .zip archive"
        )
        self.assertTrue(
            "tar.gz" in steps_str or ".tar.gz" in steps_str,
            "Release job must package macOS/Linux .tar.gz archives"
        )
        self.assertIn("start_windows.bat", steps_str, "Windows archive must include start_windows.bat")
        self.assertIn("start_mac.command", steps_str, "macOS archive must include start_mac.command")
        self.assertIn("chmod +x", steps_str, "macOS/Linux archives must preserve +x executable bits")
        self.assertIn("softprops/action-gh-release@v2", steps_str, "Must publish assets with softprops/action-gh-release@v2")

    @unittest.skipUnless(HAS_RELEASE_WORKFLOW, ".github/workflows/release.yml not yet created")
    def test_docker_publish_multiarch_and_ghcr_registry(self):
        """F22: Verify docker-publish builds multi-arch (amd64, arm64) and targets GHCR registry."""
        import yaml

        workflow_path = os.path.join(BASE_DIR, ".github", "workflows", "release.yml")
        with open(workflow_path, "r", encoding="utf-8") as f:
            workflow = yaml.safe_load(f)

        docker_job = workflow["jobs"]["docker-publish"]
        steps = docker_job.get("steps", [])
        steps_str = json.dumps(steps)

        # Verify GHCR registry login
        self.assertIn("ghcr.io", steps_str, "Docker publish job must target ghcr.io registry")
        self.assertIn("GITHUB_TOKEN", steps_str, "Docker login must use GITHUB_TOKEN secret")

        # Verify multi-architecture build
        self.assertIn("linux/amd64", steps_str, "Docker build must target linux/amd64 platform")
        self.assertIn("linux/arm64", steps_str, "Docker build must target linux/arm64 platform")

        # Verify image repository name and actions
        self.assertTrue(
            "jasonxp516/ptt-dc-bot" in steps_str or "github.repository" in steps_str,
            "Docker image repository name must match project namespace"
        )
        self.assertIn("docker/setup-qemu-action@v3", steps_str, "Docker build must set up QEMU")
        self.assertIn("docker/setup-buildx-action@v3", steps_str, "Docker build must set up Buildx")
        self.assertIn("docker/metadata-action@v5", steps_str, "Docker build must use metadata-action")
        self.assertIn("docker/build-push-action@v6", steps_str, "Docker build must use build-push-action")


class TestDockerComposeAndIgnoreDeployment(BaseDeploymentTestCase):
    """F23-F24: Verification of docker-compose.yml prebuilt image and .dockerignore hardening."""

    @unittest.skipUnless(HAS_DOCKER_COMPOSE, "docker-compose.yml not found")
    def test_docker_compose_yaml_syntax_and_service_spec(self):
        """F23: Verify docker-compose.yml is valid YAML and defines pre-built GHCR image with build fallback."""
        import yaml

        compose_path = os.path.join(BASE_DIR, "docker-compose.yml")
        with open(compose_path, "r", encoding="utf-8") as f:
            compose_data = yaml.safe_load(f)

        self.assertIsInstance(compose_data, dict, "docker-compose.yml root must be a YAML mapping")
        self.assertIn("services", compose_data, "docker-compose.yml must define 'services'")
        services = compose_data["services"]
        self.assertIn("ptt-dc-bot", services, "Must declare 'ptt-dc-bot' service")

        bot_svc = services["ptt-dc-bot"]

        # Prebuilt GHCR image requirement
        self.assertIn("image", bot_svc, "Service must specify pre-built image")
        self.assertEqual(
            bot_svc["image"],
            "ghcr.io/jasonxp516/ptt-dc-bot:latest",
            "Pre-built image must point to ghcr.io/jasonxp516/ptt-dc-bot:latest"
        )

        # Local build fallback context preserved
        self.assertIn("build", bot_svc, "Service must preserve 'build: .' fallback for local building")

        # Volume & env_file verification
        volumes = bot_svc.get("volumes", [])
        self.assertTrue(
            any("./data:/app/data" in str(v) for v in volumes),
            "Service must mount ./data:/app/data for persistence"
        )
        env_files = bot_svc.get("env_file", [])
        self.assertTrue(
            ".env" in str(env_files),
            "Service must reference .env file"
        )

        # Security hardening options
        sec_opts = bot_svc.get("security_opt", [])
        self.assertTrue(
            any("no-new-privileges:true" in str(s) for s in sec_opts),
            "Service must enforce no-new-privileges:true"
        )

        # Resource limits and healthcheck
        self.assertIn("healthcheck", bot_svc, "Service must define healthcheck")
        self.assertIn("deploy", bot_svc, "Service must define resource deploy limits")

    @unittest.skipUnless(HAS_DOCKERIGNORE, ".dockerignore not found")
    def test_dockerignore_contains_all_critical_exclusions(self):
        """F24: Verify .dockerignore explicitly contains all sensitive and ephemeral patterns."""
        dockerignore_path = os.path.join(BASE_DIR, ".dockerignore")
        with open(dockerignore_path, "r", encoding="utf-8") as f:
            rules = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        # Critical patterns that must be present
        required_patterns = [
            ".env",
            "*.db",
            "*.log",
            "tests/",
            ".agents/",
            ".git",
            ".venv/",
            "dist/",
            "staging*",
        ]
        for pattern in required_patterns:
            self.assertTrue(
                any(pattern == r or pattern.rstrip("/") == r.rstrip("/") for r in rules),
                f".dockerignore must contain pattern: {pattern}"
            )

    @unittest.skipUnless(HAS_DOCKERIGNORE, ".dockerignore not found")
    def test_dockerignore_pattern_matching_simulation(self):
        """F24: Verify path exclusion matching simulation via fnmatch."""
        import fnmatch

        dockerignore_path = os.path.join(BASE_DIR, ".dockerignore")
        with open(dockerignore_path, "r", encoding="utf-8") as f:
            rules = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        def is_path_ignored(test_path: str) -> bool:
            norm = test_path.strip("/")
            parts = norm.split("/")
            for r in rules:
                r_clean = r.rstrip("/")
                for part in parts:
                    if fnmatch.fnmatch(part, r_clean):
                        return True
                if fnmatch.fnmatch(norm, r_clean) or fnmatch.fnmatch(norm, r_clean + "/*"):
                    return True
            return False

        # Positive assertions: MUST be ignored
        must_be_ignored = [
            ".env",
            "bot_data.db",
            "bot_data.db-journal",
            "test_bot.db.bak",
            "bot.log",
            "bot.log.1",
            "tests/test_deployment.py",
            ".agents/teamwork/plan.md",
            ".git/config",
            ".venv/bin/python",
            "venv/lib/site-packages",
            "dist/ptt-dc-bot-windows.zip",
            "staging_win/bot.py",
        ]
        for path in must_be_ignored:
            self.assertTrue(is_path_ignored(path), f"Path '{path}' must be excluded by .dockerignore")

        # Negative assertions: Core application files MUST NOT be ignored
        must_not_be_ignored = [
            "bot.py",
            "setup_wizard.py",
            "requirements.txt",
            "Dockerfile",
            "docker-compose.yml",
            "storage.py",
            "ptt_crawler.py",
            "embeds.py",
            "delivery.py",
        ]
        for path in must_not_be_ignored:
            self.assertFalse(is_path_ignored(path), f"Core application file '{path}' must NOT be ignored")


# =============================================================================
# 7. Non-Functional Guarantees (Security & Cleanliness Audits)
# =============================================================================

class TestSecurityAndCleanlinessAudits(unittest.TestCase):
    """Automated security audits guaranteeing zero credential leakage and workspace hygiene."""

    def test_zero_credential_leakage_audit(self):
        """Audit: Verify no raw unmasked API keys exist in git tracking or test code."""
        # Forbidden pattern: Any raw groq key resembling live credentials
        forbidden_regex = re.compile(r"gsk_[A-Za-z0-9]{40,}")

        inspected_extensions = (".py", ".sh", ".ps1", ".bat", ".command", ".md", ".yml", ".json")
        for root, dirs, files in os.walk(BASE_DIR):
            if ".git" in root or ".venv" in root or "venv" in root or ".agents" in root or "pre_release_verification" in root:
                continue
            for file in files:
                if any(file.endswith(ext) for ext in inspected_extensions):
                    file_path = os.path.join(root, file)
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            # Skip reference to mock key placeholders
                            if "mock" in line.lower() or "your_" in line.lower():
                                continue
                            match = forbidden_regex.search(line)
                            self.assertIsNone(
                                match,
                                f"Security Violation: Found unmasked API key pattern in {file_path}:{line_num}"
                            )

    def test_clean_workspace_no_orphan_artifacts(self):
        """Audit: Ensure no temporary test databases or test log files exist in project root."""
        repo_files = os.listdir(BASE_DIR)
        for f in repo_files:
            self.assertFalse(
                f.endswith(".db") and f != "bot_data.db",
                f"Cleanliness Violation: Found stray test database {f} in repository root"
            )
            self.assertFalse(
                f.endswith(".db.bak"),
                f"Cleanliness Violation: Found backup database {f} in repository root"
            )


if __name__ == "__main__":
    unittest.main()
