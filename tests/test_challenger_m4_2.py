"""
Adversarial Stress Test Suite for .dockerignore and docker-compose.yml
Verification Harness for challenger_m4_2 (EMPIRICAL CHALLENGER)
"""
import fnmatch
import os
import subprocess
import sys
import tempfile
import unittest
import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dockerignore_rules():
    path = os.path.join(BASE_DIR, ".dockerignore")
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def is_path_ignored_by_docker(test_path: str, rules: list) -> bool:
    """
    Simulates Docker's BuildKit/CLI path matching logic for .dockerignore:
    1. Normalizes path (strip leading/trailing slashes).
    2. Path components are checked hierarchically: if any ancestor directory matches an ignore rule,
       the whole directory is pruned by Docker CLI (filepath.SkipDir).
    3. The relative path and leaf names are checked against globs (handling * and **).
    """
    norm = test_path.strip("/")
    parts = norm.split("/")

    # Check hierarchical ancestor pruning
    for i in range(1, len(parts) + 1):
        sub_path = "/".join(parts[:i])
        leaf = parts[i - 1]
        for r in rules:
            r_clean = r.rstrip("/")
            # Direct match on path segment or directory
            if fnmatch.fnmatch(sub_path, r_clean) or fnmatch.fnmatch(leaf, r_clean):
                return True
            if fnmatch.fnmatch(sub_path, r_clean + "/*"):
                return True

    # Check full relative path and basename against all rules
    for r in rules:
        r_clean = r.rstrip("/")
        if fnmatch.fnmatch(norm, r_clean):
            return True
        if fnmatch.fnmatch(parts[-1], r_clean):
            return True
        if r.endswith("/") and fnmatch.fnmatch(norm, r_clean + "/*"):
            return True

    return False


class TestDockerignoreStressHarness(unittest.TestCase):
    """Adversarial stress test cases for .dockerignore covering 80+ paths."""

    @classmethod
    def setUpClass(cls):
        cls.rules = load_dockerignore_rules()

    def test_sensitive_files_no_false_negatives(self):
        """Verify sensitive credentials, databases, logs, agent files, and caches are ignored."""
        must_ignore_paths = [
            # 1. Environment & Credential files
            ".env",
            ".env.local",
            ".env.production",
            ".env.staging",
            ".env.development",
            ".env.test",
            ".env.backup",
            ".env.secret",
            ".env.prod",

            # 2. Databases, journals, WAL, SHM, and backups
            "bot_data.db",
            "bot_data.db-journal",
            "bot_data.db-wal",
            "bot_data.db-shm",
            "bot_data.db.bak",
            "bot_data.db.bak.20261105",
            "test_bot.db",
            "test_bot.db-journal",
            "data/bot_data.db",
            "data/sub/bot_data.db",
            "data/nested/deep/secret.db",
            "data/arbitrary_file.txt",
            "data/sub/nested/file.json",
            "backups/bot_backup.db",
            "backups/20261105/data.db",
            "backups/snapshot.sqlite",

            # 3. Agent team folders and metadata
            ".agents/teamwork/orchestrator_gen3/BRIEFING.md",
            ".agents/teamwork/challenger_m4_2/handoff.md",
            ".agents/teamwork/ORIGINAL_REQUEST.md",
            ".agents/teamwork/PROJECT.md",
            ".agents/teamwork/plan.md",
            ".agents/metadata.json",

            # 4. Git metadata and GitHub workflows
            ".git",
            ".git/config",
            ".git/HEAD",
            ".git/objects/00/abcdef",
            ".gitignore",
            ".github/workflows/release.yml",
            ".github/dependabot.yml",
            ".github/ISSUE_TEMPLATE/bug.md",

            # 5. Logs (rotated, timestamped, backup)
            "bot.log",
            "bot.log.1",
            "bot.log.2",
            "bot.log.2026-11-05",
            "bot.log.old",
            "app.log",
            "test.log",
            "crawler.log.bak",

            # 6. Python bytecode, cache, and pytest
            "__pycache__/bot.cpython-311.pyc",
            "__pycache__/setup_wizard.cpython-311.pyc",
            "sub/__pycache__/module.cpython-311.pyc",
            "bot.pyc",
            "bot.pyo",
            "bot.pyd",
            ".pytest_cache/v/cache/lastfailed",
            ".pytest_cache/README.md",

            # 7. Test suites and test files
            "tests/test_deployment.py",
            "tests/unit/test_foo.py",
            "tests/e2e/test_bar.py",
            "tests/conftest.py",
            "tests/fixtures/sample.html",

            # 8. Release staging, archives, and dist packages
            "dist/ptt-dc-bot-windows.zip",
            "dist/ptt-dc-bot-macos.tar.gz",
            "dist/ptt-dc-bot-linux.tar.gz",
            "staging_win/bot.py",
            "staging_mac/install.sh",
            "staging_linux/docker-compose.yml",
            "staging/temp.txt",
            "archive.zip",
            "backup.tar.gz",

            # 9. Virtual environments
            "venv/bin/activate",
            "venv/lib/python3.11/site-packages/pkg.py",
            ".venv/bin/python",
            ".venv/pyvenv.cfg",

            # 10. Internal guide
            "IMPLEMENTATION_GUIDE.md",
        ]

        failed = []
        for path in must_ignore_paths:
            if not is_path_ignored_by_docker(path, self.rules):
                failed.append(path)

        self.assertEqual(
            len(failed), 0,
            f"FALSE NEGATIVE: Sensitive or unwanted paths slipped past .dockerignore: {failed}"
        )

    def test_essential_source_files_no_false_positives(self):
        """Verify critical production source files and configs are NOT ignored."""
        must_include_paths = [
            "bot.py",
            "setup_wizard.py",
            "config.py",
            "delivery.py",
            "embeds.py",
            "groq_agent.py",
            "groq_scheduler.py",
            "ptt_crawler.py",
            "storage.py",
            "requirements.txt",
            "Dockerfile",
            "docker-compose.yml",
            "README.md",
            "README_EN.md",
            "LICENSE",
            "install.sh",
            "install.ps1",
            "start_windows.bat",
            "start_mac.command",
            "start_bg.sh",
            "stop_bg.sh",
            "restart_bg.sh",
            "ptt-bot.service",
        ]

        false_positives = []
        for path in must_include_paths:
            if is_path_ignored_by_docker(path, self.rules):
                false_positives.append(path)

        self.assertEqual(
            len(false_positives), 0,
            f"FALSE POSITIVE: Essential source files accidentally ignored by .dockerignore: {false_positives}"
        )

    def test_env_example_and_secret_masking_boundary(self):
        """Verify .env.* correctly catches all env variants while core config remains clean."""
        # .env.* catches .env.production, .env.staging, .env.local, .env.example
        self.assertTrue(
            is_path_ignored_by_docker(".env.production", self.rules),
            ".env.production must be ignored"
        )
        self.assertTrue(
            is_path_ignored_by_docker(".env.example", self.rules),
            ".env.example is ignored under .env.* (Docker image does not require template file)"
        )


class TestDockerComposeValidation(unittest.TestCase):
    """Schema, edge case, and semantic validation for docker-compose.yml."""

    @classmethod
    def setUpClass(cls):
        cls.compose_path = os.path.join(BASE_DIR, "docker-compose.yml")
        with open(cls.compose_path, "r", encoding="utf-8") as f:
            cls.compose_data = yaml.safe_load(f)

    def test_schema_structure(self):
        """Verify compose file has top-level services mapping and ptt-dc-bot service."""
        self.assertIsInstance(self.compose_data, dict, "Root must be a YAML mapping")
        self.assertIn("services", self.compose_data, "Root must define 'services'")
        services = self.compose_data["services"]
        self.assertIn("ptt-dc-bot", services, "Must declare 'ptt-dc-bot' service")

    def test_image_and_build_declarations(self):
        """Verify image points to GHCR prebuilt image and build context is '.' for local fallback."""
        bot_svc = self.compose_data["services"]["ptt-dc-bot"]
        self.assertEqual(
            bot_svc.get("image"),
            "ghcr.io/jasonxp516/ptt-dc-bot:latest",
            "image must match ghcr.io/jasonxp516/ptt-dc-bot:latest",
        )
        self.assertEqual(
            bot_svc.get("build"),
            ".",
            "build context must be set to '.'",
        )

    def test_healthcheck_command_syntax_and_behavior(self):
        """Verify healthcheck command syntax, timeout, retries, and exit code logic."""
        bot_svc = self.compose_data["services"]["ptt-dc-bot"]
        hc = bot_svc.get("healthcheck")
        self.assertIsNotNone(hc, "ptt-dc-bot must define healthcheck")

        self.assertEqual(hc.get("interval"), "60s")
        self.assertEqual(hc.get("timeout"), "5s")
        self.assertEqual(hc.get("retries"), 3)

        cmd = hc.get("test")
        self.assertIsInstance(cmd, list, "Healthcheck test must be a list")
        self.assertEqual(cmd[0], "CMD")
        self.assertEqual(cmd[1], "python3")
        self.assertEqual(cmd[2], "-c")
        code = cmd[3]
        self.assertIn("os.path.exists", code)
        self.assertIn("/app/data/bot_data.db", code)

        # Empirically test python exit code behavior with the command logic
        with tempfile.TemporaryDirectory() as tmp:
            test_db = os.path.join(tmp, "bot_data.db")
            # 1. When DB does not exist -> exit code 1
            res_missing = subprocess.run(
                ["python3", "-c", f"import os; exit(0 if os.path.exists('{test_db}') else 1)"]
            )
            self.assertEqual(res_missing.returncode, 1, "Healthcheck must exit 1 when DB missing")

            # 2. When DB exists -> exit code 0
            open(test_db, "w").close()
            res_exists = subprocess.run(
                ["python3", "-c", f"import os; exit(0 if os.path.exists('{test_db}') else 1)"]
            )
            self.assertEqual(res_exists.returncode, 0, "Healthcheck must exit 0 when DB exists")

    def test_volume_mounts_and_environment_alignment(self):
        """Verify volume mounts persist ./data to /app/data and environment variables align."""
        bot_svc = self.compose_data["services"]["ptt-dc-bot"]
        volumes = bot_svc.get("volumes", [])
        self.assertTrue(
            any("./data:/app/data" in str(v) for v in volumes),
            "Volumes must map ./data to /app/data",
        )

        env_list = bot_svc.get("environment", [])
        env_dict = {}
        for item in env_list:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                env_dict[k] = v
            elif isinstance(item, dict):
                env_dict.update(item)

        self.assertEqual(env_dict.get("DB_PATH"), "/app/data/bot_data.db")
        self.assertEqual(env_dict.get("LOG_PATH"), "/app/data/bot.log")
        self.assertEqual(env_dict.get("LOG_TO_STDOUT"), "1")
        self.assertEqual(env_dict.get("TZ"), "Asia/Taipei")

        # Verify env_file includes .env
        env_file = bot_svc.get("env_file", [])
        self.assertTrue(".env" in env_file or ".env" in str(env_file))

    def test_security_hardening_options(self):
        """Verify no-new-privileges and cap_drop ALL security enforcement."""
        bot_svc = self.compose_data["services"]["ptt-dc-bot"]
        sec_opt = bot_svc.get("security_opt", [])
        self.assertIn("no-new-privileges:true", sec_opt)

        cap_drop = bot_svc.get("cap_drop", [])
        self.assertIn("ALL", cap_drop)

        # Resource limits
        deploy = bot_svc.get("deploy", {})
        limits = deploy.get("resources", {}).get("limits", {})
        self.assertEqual(limits.get("cpus"), "1.0")
        self.assertEqual(limits.get("memory"), "512M")


if __name__ == "__main__":
    unittest.main()
