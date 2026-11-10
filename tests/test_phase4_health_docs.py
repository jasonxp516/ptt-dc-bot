import os
import subprocess
import tempfile
import time
from pathlib import Path

import yaml

import config

if not os.environ.get("LOG_PATH") or not os.environ.get("DB_PATH"):
    _TMPDIR = tempfile.TemporaryDirectory()
    if not os.environ.get("LOG_PATH"):
        config.LOG_PATH = os.path.join(_TMPDIR.name, "bot.log")
        os.environ["LOG_PATH"] = config.LOG_PATH
    if not os.environ.get("DB_PATH"):
        config.DB_PATH = os.path.join(_TMPDIR.name, "bot_data.db")
        os.environ["DB_PATH"] = config.DB_PATH

from bot import PTTDiscordBot


ROOT = Path(__file__).resolve().parents[1]


def test_write_heartbeat_writes_current_unix_timestamp(tmp_path, monkeypatch):
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setenv("HEARTBEAT_FILE", str(heartbeat))
    bot = object.__new__(PTTDiscordBot)

    before = int(time.time())
    bot._write_heartbeat()
    after = int(time.time())

    value = int(heartbeat.read_text(encoding="utf-8"))
    assert before <= value <= after


def _healthcheck_exit(tmp_path, heartbeat_age=None):
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "bot_data.db").touch()
    heartbeat = data / "heartbeat"
    if heartbeat_age is not None:
        heartbeat.touch()
        timestamp = time.time() - heartbeat_age
        os.utime(heartbeat, (timestamp, timestamp))

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    command = compose["services"]["ptt-dc-bot"]["healthcheck"]["test"]
    assert command[:2] == ["CMD", "python3"]
    script = command[3].replace("/app/data", str(data))
    env = os.environ.copy()
    env["HEARTBEAT_FILE"] = str(heartbeat)
    return subprocess.run(
        [command[1], command[2], script], env=env, check=False
    ).returncode


def test_compose_healthcheck_requires_fresh_heartbeat(tmp_path):
    assert _healthcheck_exit(tmp_path / "fresh", heartbeat_age=1) == 0
    assert _healthcheck_exit(tmp_path / "missing", heartbeat_age=None) == 1
    assert _healthcheck_exit(tmp_path / "stale", heartbeat_age=601) == 1


def test_phase4_documents_and_ci_exist():
    required = [
        "docs/deploy-docker.md",
        "docs/deploy-synology.md",
        "docs/deploy-unraid.md",
        "docs/deploy-linux.md",
        "PRIVACY.md",
        ".github/workflows/ci.yml",
    ]
    assert all((ROOT / path).is_file() for path in required)


def test_readme_assets_length_and_discord_intent_guidance():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for asset in (
        "assets/setup_control_panel.svg",
        "assets/dropdown_boards_select.svg",
        "assets/keyword_modal_form.svg",
        "assets/notification_embed_preview.svg",
    ):
        assert asset in readme
    assert len(readme.splitlines()) < 360
    assert "Message Content Intent" in readme
    assert "不需要" in readme
