#!/usr/bin/env python3
"""
PTT-DC-Bot Multi-Platform Setup Wizard
---------------------------------------
A lightweight, dependency-free interactive configuration wizard for PTT-DC-Bot.
Supports Discord, Telegram, and Email (SMTP) notification platforms.
Uses ONLY Python standard library modules (urllib, smtplib, ssl, socket, json, os, sys, stat, re).
Strictly zero decorative emojis.
"""

import json
import os
import re
import smtplib
import socket
import ssl
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple, Union

# Windows console UTF-8 reconfiguration
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def is_interactive() -> bool:
    """Safely check if standard input is attached to an interactive terminal."""
    try:
        if not sys.stdin or getattr(sys.stdin, "closed", False) is True:
            return False
        return bool(hasattr(sys.stdin, "isatty") and sys.stdin.isatty())
    except (ValueError, AttributeError, OSError):
        return False


# Constants
PERMISSIONS_INTEGER = 274877975552
OAUTH2_SCOPE = "bot%20applications.commands"
DISCORD_API_ME_URL = "https://discord.com/api/v10/users/@me"
TELEGRAM_API_BASE_URL = "https://api.telegram.org"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
USER_AGENT = "PTT-DC-Bot-SetupWizard"
DEFAULT_BOARD_NAME = "HardwareSale"
DEFAULT_LANGUAGE = "zh_TW"
SUPPORTED_LANGUAGES = {
    "1": "zh_TW",
    "2": "en",
    "zh_tw": "zh_TW",
    "zh-tw": "zh_TW",
    "zh": "zh_TW",
    "tw": "zh_TW",
    "en": "en",
    "en_us": "en",
    "en-us": "en",
    "english": "en",
}

# RFC 5322 Compliant Email Regex
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9]+([.-][a-zA-Z0-9]+)*\.[a-zA-Z]{2,}$"
)


def generate_oauth2_url(client_id: str) -> str:
    """Generate Discord bot invite URL with appropriate permissions and scope."""
    cleaned_id = str(client_id).strip()
    return (
        f"https://discord.com/oauth2/authorize?client_id={cleaned_id}"
        f"&permissions={PERMISSIONS_INTEGER}&scope={OAUTH2_SCOPE}"
    )


def parse_platform_selection(raw_input: str) -> Tuple[List[str], Optional[str]]:
    """
    Parse user platform selection input.
    Supports: '1', '2', '3', combinations like '1,2', '1,3', '2,3', '1,2,3',
              keywords 'all', '全部', '*', 'discord', 'telegram', 'email'.
    Default: empty string '' returns (['discord'], None).
    Legacy Discord token detection: if input is a legacy Discord token (or token-like string),
    returns (['discord'], cleaned_token).
    Quit keywords ('q', 'quit', 'exit') return ([], None).
    """
    cleaned = str(raw_input or "").strip()
    if not cleaned:
        return ["discord"], None

    lower = cleaned.lower()
    if lower in ("q", "quit", "exit"):
        return [], None

    if lower in ("all", "全部", "*"):
        return ["discord", "telegram", "email"], None

    platform_map = {
        "1": "discord",
        "discord": "discord",
        "dc": "discord",
        "2": "telegram",
        "telegram": "telegram",
        "tg": "telegram",
        "3": "email",
        "email": "email",
        "mail": "email",
        "smtp": "email",
    }

    tokens = [t.strip().lower() for t in re.split(r"[,+\s、]+", cleaned) if t.strip()]
    if tokens and all(t in platform_map for t in tokens):
        selected_set = {platform_map[t] for t in tokens}
        ordered = [p for p in ("discord", "telegram", "email") if p in selected_set]
        return ordered, None

    # Legacy Discord token auto-detection:
    # Treat unrecognized non-quit input as a candidate Discord token for backward compatibility
    cleaned_token = re.sub(r"^[Bb][Oo][Tt](\s+|$)", "", cleaned).strip()
    return ["discord"], cleaned_token


def validate_discord_token(
    token: str, timeout: float = 10.0
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Validate Discord bot token against GET https://discord.com/api/v10/users/@me.

    Returns:
        (is_valid, bot_id, username, error_message)
    """
    cleaned_token = re.sub(r"^[Bb][Oo][Tt](\s+|$)", "", token.strip()).strip()
    if not cleaned_token:
        return False, None, None, "Token 不能為空"

    req = urllib.request.Request(
        DISCORD_API_ME_URL,
        headers={
            "Authorization": f"Bot {cleaned_token}",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                if not isinstance(data, dict):
                    return False, None, None, "Discord API 回應格式不正確"

                raw_id = data.get("id")
                if not raw_id:
                    return False, None, None, "Discord API 回應缺少有效的 Bot ID"

                bot_id = str(raw_id).strip()
                if not bot_id or bot_id.lower() == "none":
                    return False, None, None, "Discord API 回應缺少有效的 Bot ID"

                raw_username = data.get("username")
                username = str(raw_username).strip() if raw_username else "Discord Bot"
                if not username or username.lower() == "none":
                    username = "Discord Bot"

                return True, bot_id, username, None
            return False, None, None, f"非預期的 HTTP 狀態碼: {response.status}"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, None, None, "401 Unauthorized：Token 無效或不存在，請確認金鑰是否正確複製"
        if e.code == 403:
            return False, None, None, "403 Forbidden：此 Bot 權限不足或缺少必要的權限設定"
        return False, None, None, f"HTTP 錯誤 ({e.code}): {e.reason}"
    except urllib.error.URLError as e:
        return False, None, None, f"網路連線失敗: {e.reason}"
    except Exception as e:
        return False, None, None, f"驗證時發生未知例外: {e}"


def validate_telegram_token(
    token: str, timeout: float = 10.0
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Validate Telegram Bot token against GET https://api.telegram.org/bot<token>/getMe.
    Zero external dependencies (uses standard library urllib.request and json).

    Returns:
        (is_valid, bot_id, username, error_message)
    """
    cleaned_token = str(token or "").strip()
    if cleaned_token.lower().startswith("bot"):
        cleaned_token = cleaned_token[3:].lstrip(":").strip()

    if not cleaned_token:
        return False, None, None, "Token 不能為空"

    if ":" not in cleaned_token:
        return (
            False,
            None,
            None,
            "Telegram Token 格式不正確 (應為 <bot_id>:<secret>，例如 123456789:ABCdef...)",
        )

    parts = cleaned_token.split(":", 1)
    if not parts[0].isdigit():
        return (
            False,
            None,
            None,
            "Telegram Token 格式不正確：冒號前應為純數字之 Bot ID",
        )

    url = f"{TELEGRAM_API_BASE_URL}/bot{cleaned_token}/getMe"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                if not isinstance(data, dict) or not data.get("ok"):
                    return False, None, None, "Telegram API 回應非預期格式或認證失敗"

                result = data.get("result", {})
                raw_id = result.get("id")
                if not raw_id:
                    return False, None, None, "Telegram API 回應缺少有效的 Bot ID"

                bot_id = str(raw_id).strip()
                username = str(
                    result.get("username")
                    or result.get("first_name")
                    or "Telegram Bot"
                ).strip()

                return True, bot_id, username, None
            return False, None, None, f"非預期的 HTTP 狀態碼: {response.status}"

    except urllib.error.HTTPError as e:
        if e.code == 401:
            return (
                False,
                None,
                None,
                "401 Unauthorized：Telegram Bot Token 無效或已撤銷，請向 @BotFather 確認金鑰是否正確",
            )
        if e.code == 404:
            return (
                False,
                None,
                None,
                "404 Not Found：查無此 Telegram API 端點或 Token 格式錯誤",
            )
        if e.code == 429:
            return (
                False,
                None,
                None,
                "429 Too Many Requests：Telegram API 請求頻率過高，請稍候重試",
            )
        try:
            err_data = json.loads(e.read().decode("utf-8"))
            desc = err_data.get("description", e.reason)
        except Exception:
            desc = e.reason
        return False, None, None, f"HTTP 錯誤 ({e.code}): {desc}"

    except urllib.error.URLError as e:
        return False, None, None, f"網路連線失敗: {e.reason}"
    except (socket.timeout, TimeoutError):
        return False, None, None, f"連線超時 (超過 {timeout} 秒)，請檢查網路或 Proxy 設定"
    except Exception as e:
        return False, None, None, f"驗證時發生未知例外: {e}"


def is_valid_telegram_chat_id(chat_id: str) -> bool:
    """
    Validate Telegram Chat ID format offline.
    Supports user ID (positive integer), group ID (negative integer),
    supergroup/channel ID (-100...), and public channel username (@channel).
    """
    cleaned = str(chat_id or "").strip()
    if not cleaned:
        return False
    if re.match(r"^-?\d{5,20}$", cleaned):
        return True
    if re.match(r"^@[a-zA-Z0-9_]{5,32}$", cleaned):
        return True
    return False


def validate_telegram_chat_id(
    token: str, chat_id: str, timeout: float = 10.0
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validate Telegram Chat ID against GET https://api.telegram.org/bot<token>/getChat?chat_id=<chat_id>.
    Returns:
        (is_valid, chat_title_or_username, error_message)
    """
    cleaned_token = str(token or "").strip()
    cleaned_chat = str(chat_id or "").strip()

    if not cleaned_token:
        return False, None, "Bot Token 不能為空"
    if not cleaned_chat:
        return False, None, "Chat ID 不能為空"

    if not is_valid_telegram_chat_id(cleaned_chat):
        return (
            False,
            None,
            "Chat ID 格式不正確 (應為數字 ID 例如 123456789、群組 ID -1001234567890，或公開頻道 @名稱)",
        )

    query = urllib.parse.urlencode({"chat_id": cleaned_chat})
    url = f"{TELEGRAM_API_BASE_URL}/bot{cleaned_token}/getChat?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("ok"):
                    res = data.get("result", {})
                    title = (
                        res.get("title")
                        or res.get("username")
                        or res.get("first_name")
                        or cleaned_chat
                    )
                    return True, str(title), None
                return False, None, "Telegram API 回傳非預期資料格式"
            return False, None, f"非預期的 HTTP 狀態碼: {response.status}"

    except urllib.error.HTTPError as e:
        if e.code == 400:
            return (
                False,
                None,
                "400 Bad Request：查無此聊天室 ID。請確認您已在 Telegram 開啟機器人並發送 /start，或已將機器人加入該群組/頻道。",
            )
        if e.code == 401:
            return False, None, "401 Unauthorized：Bot Token 無效"
        return False, None, f"HTTP 錯誤 ({e.code}): {e.reason}"
    except urllib.error.URLError as e:
        return False, None, f"網路連線失敗: {e.reason}"
    except (socket.timeout, TimeoutError):
        return False, None, f"連線超時 (超過 {timeout} 秒)"
    except Exception as e:
        return False, None, f"驗證時發生未知例外: {e}"


def is_valid_email(email: str) -> bool:
    """
    Validate email address syntax against RFC 5322 constraints.
    Guards against Header Injection (newlines, control characters) and length limits.
    """
    if not email or not isinstance(email, str):
        return False
    cleaned = email.strip()
    if not cleaned or len(cleaned) > 254:
        return False
    # Reject header injection carriage returns, newlines, tabs, and spaces
    if any(c in cleaned for c in ("\r", "\n", "\t", " ")):
        return False

    parts = cleaned.split("@")
    if len(parts) != 2:
        return False
    local_part, domain_part = parts
    if not local_part or len(local_part) > 64 or not domain_part:
        return False
    if local_part.startswith(".") or local_part.endswith(".") or ".." in local_part:
        return False

    return bool(EMAIL_REGEX.match(cleaned))


def parse_and_validate_recipients(
    raw_input: str,
) -> Tuple[bool, List[str], Optional[str]]:
    """
    Parse and validate comma- or semicolon-separated recipient emails for EMAIL_TO.
    Deduplicates entries while preserving order.

    Returns:
        (is_valid, list_of_clean_emails, error_message)
    """
    cleaned = str(raw_input or "").strip()
    if not cleaned:
        return False, [], "收件者地址不能為空"

    tokens = re.split(r"[,;]+", cleaned)
    valid_list: List[str] = []
    seen = set()

    for token in tokens:
        item = token.strip()
        if not item:
            continue
        if not is_valid_email(item):
            return False, [], f"電子郵件地址格式不正確: '{item}'"
        lower = item.lower()
        if lower not in seen:
            seen.add(lower)
            valid_list.append(item)

    if not valid_list:
        return False, [], "未包含任何有效的電子郵件地址"

    return True, valid_list, None


def validate_smtp_credentials(
    host: str,
    port: Union[int, str],
    user: str,
    password: str,
    use_tls: bool = True,
    timeout: float = 10.0,
) -> Tuple[bool, Optional[str]]:
    """
    Validate SMTP host, port, TLS/SSL connection, and optional login credentials.
    Zero external dependencies (uses standard library smtplib, ssl, socket).

    Returns:
        (is_valid, error_message)
    """
    cleaned_host = str(host or "").strip()
    if not cleaned_host:
        return False, "SMTP 伺服器主機 (Host) 不能為空"

    try:
        port_num = int(port)
        if port_num < 1 or port_num > 65535:
            return False, "SMTP 連接埠 (Port) 必須為 1 到 65535 之間的整數"
    except (ValueError, TypeError):
        return False, "SMTP 連接埠 (Port) 必須為有效整數"

    cleaned_user = str(user or "").strip()
    cleaned_pass = str(password or "").strip()

    try:
        ssl_context = ssl.create_default_context()

        if port_num == 465:
            # SMTPS: Implicit TLS / SSL connection
            with smtplib.SMTP_SSL(
                cleaned_host, port_num, context=ssl_context, timeout=timeout
            ) as server:
                server.ehlo()
                if cleaned_user and cleaned_pass:
                    server.login(cleaned_user, cleaned_pass)
        else:
            # Standard SMTP / Submission (typically Port 587 or 25)
            with smtplib.SMTP(cleaned_host, port_num, timeout=timeout) as server:
                server.ehlo()
                if use_tls:
                    server.starttls(context=ssl_context)
                    server.ehlo()
                if cleaned_user and cleaned_pass:
                    server.login(cleaned_user, cleaned_pass)

        return True, None

    except smtplib.SMTPAuthenticationError as e:
        smtp_code = getattr(e, "smtp_code", 535)
        return (
            False,
            f"SMTP 認證失敗 (代碼 {smtp_code})：帳號或密碼錯誤。"
            "若使用 Gmail，請確認已啟用「兩步驟驗證」並在 Google 帳戶安全性設定中產生 16 碼「應用程式密碼」(App Password)，勿使用一般登入密碼。",
        )
    except smtplib.SMTPConnectError as e:
        return False, f"無法連線至 SMTP 伺服器 ({cleaned_host}:{port_num})：連線建立失敗 ({e})"
    except smtplib.SMTPServerDisconnected as e:
        return (
            False,
            f"SMTP 伺服器意外中斷連線 ({cleaned_host}:{port_num})。請確認連接埠與 TLS 設定是否匹配 (例如 Port 465 需使用 SSL，Port 587 需使用 STARTTLS): {e}",
        )
    except smtplib.SMTPNotSupportedError as e:
        return False, f"SMTP 伺服器不支援 STARTTLS 加密通道: {e}"
    except smtplib.SMTPHeloError as e:
        return False, f"SMTP HELO/EHLO 握手遭伺服器拒絕: {e}"
    except (ssl.SSLCertVerificationError, ssl.CertificateError) as e:
        return False, f"SSL/TLS 憑證驗證失敗：伺服器憑證無效或過期: {e}"
    except ssl.SSLError as e:
        return False, f"SSL/TLS 加密通道握手失敗 ({cleaned_host}:{port_num}): {e}"
    except (socket.timeout, TimeoutError):
        return (
            False,
            f"連線至 SMTP 伺服器超時 (超過 {timeout} 秒)，請檢查主機位址、連接埠或網路防火牆",
        )
    except ConnectionRefusedError:
        return (
            False,
            f"連線遭拒絕 (Connection Refused)：目標主機 ({cleaned_host}) 在連接埠 {port_num} 上未提供 SMTP 服務",
        )
    except socket.gaierror as e:
        return False, f"DNS 主機名稱解析失敗：找不到主機 '{cleaned_host}'，請檢查拼字: {e}"
    except smtplib.SMTPException as e:
        return False, f"SMTP 協定通訊錯誤: {e}"
    except OSError as e:
        return False, f"網路通訊端錯誤: {e}"
    except Exception as e:
        return False, f"驗證時發生未知例外: {e}"


def validate_groq_api_key(
    api_key: str, timeout: float = 10.0
) -> Tuple[bool, Optional[str]]:
    """
    Validate Groq API key against GET https://api.groq.com/openai/v1/models.

    Returns:
        (is_valid, error_message)
    """
    cleaned_key = api_key.strip()
    if not cleaned_key:
        return False, "API Key 不能為空"

    req = urllib.request.Request(
        GROQ_MODELS_URL,
        headers={
            "Authorization": f"Bearer {cleaned_key}",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                    return True, None
                return False, "Groq API 回應格式不符合預期（缺少模型清單）"
            return False, f"非預期的 HTTP 狀態碼: {response.status}"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "401 Unauthorized：Groq API Key 無效，請檢查金鑰"
        return False, f"HTTP 錯誤 ({e.code}): {e.reason}"
    except urllib.error.URLError as e:
        return False, f"網路連線失敗: {e.reason}"
    except Exception as e:
        return False, f"驗證時發生未知例外: {e}"


def is_valid_board_name(name: str) -> bool:
    """Validate PTT board name characters."""
    if not name or len(name) > 40:
        return False
    return bool(re.match(r"^[A-Za-z0-9_\-]+$", name.strip()))


def write_env_file(
    env_path: str,
    discord_token: str = "",
    groq_api_key: str = "",
    default_board: str = DEFAULT_BOARD_NAME,
    poll_interval_min: int = 90,
    poll_interval_max: int = 150,
    groq_model: str = "openai/gpt-oss-120b",
    groq_fast_model: str = "openai/gpt-oss-20b",
    bot_language: str = "zh_TW",
    *,
    enable_discord: Optional[bool] = None,
    default_channel_id: str = "",
    default_guild_id: str = "",
    discord_channel_id: str = "",
    discord_guild_id: str = "",
    owner_user_id: str = "",
    enable_telegram: bool = False,
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
    telegram_admin_user_id: str = "",
    enable_email: bool = False,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
    smtp_user: str = "",
    smtp_password: str = "",
    smtp_use_tls: bool = True,
    email_from: str = "",
    email_from_name: str = "PTT 智慧追蹤機器人",
    email_to: str = "",
) -> bool:
    """
    Write environment configuration to .env with UTF-8 encoding.
    Applies POSIX 0600 permissions (stat.S_IRUSR | stat.S_IWUSR) if not running on Windows.
    """
    # Auto-detect Discord enablement for legacy backward compatibility
    if enable_discord is None:
        enable_discord = bool(discord_token.strip()) or (not enable_telegram and not enable_email)

    eff_channel_id = (discord_channel_id or default_channel_id).strip()
    eff_guild_id = (discord_guild_id or default_guild_id).strip()
    eff_email_from = (email_from or smtp_user).strip()

    content = (
        "# =====================================================================\n"
        "# PTT-DC-Bot 多平台環境設定檔 (由設定精靈 setup_wizard.py 自動產生)\n"
        "# =====================================================================\n\n"
        "# ---------------------------------------------------------------------\n"
        "# 通用基本設定 (General Settings)\n"
        "# ---------------------------------------------------------------------\n"
        f"BOT_LANGUAGE={(bot_language or DEFAULT_LANGUAGE).strip()}\n"
        f"DEFAULT_BOARD={default_board.strip()}\n"
        f"POLL_INTERVAL_MIN={poll_interval_min}\n"
        f"POLL_INTERVAL_MAX={poll_interval_max}\n\n"
        "# ---------------------------------------------------------------------\n"
        "# Discord 平台設定 (Discord Platform Settings)\n"
        "# ---------------------------------------------------------------------\n"
        f"ENABLE_DISCORD={'true' if enable_discord else 'false'}\n"
        f"DISCORD_TOKEN={discord_token.strip()}\n"
        f"DEFAULT_CHANNEL_ID={eff_channel_id}\n"
        f"DEFAULT_GUILD_ID={eff_guild_id}\n"
        f"DISCORD_CHANNEL_ID={eff_channel_id}\n"
        f"DISCORD_GUILD_ID={eff_guild_id}\n"
        f"OWNER_USER_ID={owner_user_id.strip()}\n\n"
        "# ---------------------------------------------------------------------\n"
        "# Telegram 平台設定 (Telegram Platform Settings)\n"
        "# ---------------------------------------------------------------------\n"
        f"ENABLE_TELEGRAM={'true' if enable_telegram else 'false'}\n"
        f"TELEGRAM_BOT_TOKEN={telegram_bot_token.strip()}\n"
        f"TELEGRAM_CHAT_ID={telegram_chat_id.strip()}\n"
        f"TELEGRAM_ADMIN_USER_ID={telegram_admin_user_id.strip()}\n"
        "TELEGRAM_BLOCKED_USER_IDS=\n\n"
        "# ---------------------------------------------------------------------\n"
        "# Email SMTP 平台設定 (Email SMTP Settings)\n"
        "# ---------------------------------------------------------------------\n"
        f"ENABLE_EMAIL={'true' if enable_email else 'false'}\n"
        f"SMTP_HOST={smtp_host.strip()}\n"
        f"SMTP_PORT={smtp_port}\n"
        f"SMTP_USER={smtp_user.strip()}\n"
        f"SMTP_PASSWORD={smtp_password.strip()}\n"
        f"SMTP_USE_TLS={'true' if smtp_use_tls else 'false'}\n"
        f"EMAIL_FROM={eff_email_from}\n"
        f"EMAIL_FROM_NAME={email_from_name.strip()}\n"
        f"EMAIL_TO={email_to.strip()}\n\n"
        "# ---------------------------------------------------------------------\n"
        "# Groq Cloud API 配置 (AI 自然語言助理與語意審核)\n"
        "# ---------------------------------------------------------------------\n"
        f"GROQ_API_KEY={groq_api_key.strip()}\n"
        f"GROQ_MODEL={groq_model.strip()}\n"
        f"GROQ_FAST_MODEL={groq_fast_model.strip()}\n"
    )

    try:
        abs_env_path = os.path.abspath(env_path)
        env_dir = os.path.dirname(abs_env_path)
        if env_dir:
            os.makedirs(env_dir, mode=stat.S_IRWXU, exist_ok=True)

        old_umask = None
        if os.name != "nt":
            try:
                old_umask = os.umask(0o077)
            except Exception:
                pass

        try:
            with open(abs_env_path, "w", encoding="utf-8") as f:
                f.write(content)
        finally:
            if old_umask is not None:
                try:
                    os.umask(old_umask)
                except Exception:
                    pass

        # Set secure 0600 permissions on POSIX
        if os.name != "nt":
            try:
                os.chmod(abs_env_path, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass

        return True
    except Exception as e:
        try:
            if sys.stderr and hasattr(sys.stderr, "write"):
                sys.stderr.write(f"寫入 .env 檔案失敗: {e}\n")
        except (ValueError, AttributeError, OSError):
            pass
        return False


def run_wizard(env_path: Optional[str] = None) -> bool:
    """
    Run interactive setup wizard CLI.
    Returns True on success, False on abort or critical error.
    """
    if env_path is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        env_path = os.path.join(base_dir, ".env")

    print("\n" + "=" * 70)
    print("  [精靈] 歡迎使用 PTT-DC-Bot 首次設定精靈 (Setup Wizard)")
    print("=" * 70)
    print("本精靈將引導您完成跨平台通知與系統參數設定。")
    print("支援平台：Discord、Telegram、Email (SMTP)。")
    print("隨時可輸入 q 退出設定。\n")

    if not is_interactive():
        try:
            if sys.stderr and hasattr(sys.stderr, "write"):
                sys.stderr.write("錯誤：設定精靈需要互動式終端機 (TTY)。\n")
        except (ValueError, AttributeError, OSError):
            pass
        return False

    # -------------------------------------------------------------
    # Step 1: Platform Selection
    # -------------------------------------------------------------
    print("【步驟 1】選擇要啟用的通知平台 (Platform Selection)")
    print("  [1] Discord   - 支援伺服器頻道推播、個人私訊 DM、視覺化按鈕與斜線指令 (預設)")
    print("  [2] Telegram  - 支援個人聊天室、群組頻道即時推播、斜線指令與按鈕選單")
    print("  [3] Email     - 支援 SMTP / Gmail 即時 HTML 郵件提醒 (適合手機與公務通知)\n")
    print("  [提示] 可輸入單一編號、組合 (如 1,2 或 1,3 或 2,3) 或輸入 all 啟用全部平台。")
    print("  [提示] 直接按 [Enter] 將採用預設值: [1] Discord")

    try:
        platform_input = input("請選擇要啟用的通知平台 [1]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n\n[精靈已取消] 設定未完成。")
        return False

    if platform_input.lower() in ("q", "quit", "exit"):
        print("[精靈已取消] 設定未完成。")
        return False

    selected_platforms, prefilled_discord_token = parse_platform_selection(platform_input)
    if not selected_platforms:
        print("[精靈已取消] 設定未完成。")
        return False

    enable_discord = "discord" in selected_platforms
    enable_telegram = "telegram" in selected_platforms
    enable_email = "email" in selected_platforms

    discord_token = ""
    telegram_bot_token = ""
    telegram_chat_id = ""
    smtp_host = "smtp.gmail.com"
    smtp_port = 587
    smtp_user = ""
    smtp_password = ""
    smtp_use_tls = True
    email_to = ""
    groq_api_key = ""
    default_board = DEFAULT_BOARD_NAME
    bot_language = DEFAULT_LANGUAGE

    # -------------------------------------------------------------
    # Step: Discord Configuration (if selected)
    # -------------------------------------------------------------
    if enable_discord:
        print("\n" + "-" * 70)
        print("【步驟】配置 Discord 平台")
        print("  1. 前往 Discord 開發者入口: https://discord.com/developers/applications")
        print("  2. 建立或選擇您的 Application，點選左側「Bot」分頁")
        print("  3. 點選「Reset Token」獲取金鑰並複製")
        print("  4. [提示] 無需開啟任何「Privileged Gateway Intents」（包含 MESSAGE CONTENT INTENT）：")
        print("     - 本機器人僅使用 Slash 指令、私訊 (DM) 與 @Bot 提及，Discord 官方預設即提供完整訊息內容\n")

        candidate_token = prefilled_discord_token
        while True:
            if candidate_token:
                user_token = candidate_token
                candidate_token = None
            else:
                try:
                    user_token = input("請貼上 Discord Bot Token (或輸入 q 退出): ").strip()
                except (KeyboardInterrupt, EOFError):
                    print("\n\n[精靈已取消] 設定未完成。")
                    return False

                if not user_token:
                    print("[錯誤] 輸入不能為空，請重新輸入。\n")
                    continue
                if user_token.lower() in ("q", "quit", "exit"):
                    print("[精靈已取消] 設定未完成。")
                    return False

            cleaned_token = re.sub(r"^[Bb][Oo][Tt](\s+|$)", "", user_token).strip()
            if not cleaned_token:
                print("[錯誤] 輸入不能為空，請重新輸入。\n")
                continue

            print("[驗證中] 正在向 Discord API 驗證 Token...")
            valid, b_id, b_user, err = validate_discord_token(cleaned_token)
            if valid:
                discord_token = cleaned_token
                bot_id = b_id or ""
                bot_username = b_user or "Discord Bot"
                print(f"[成功] Token 驗證成功！機器人名稱: {bot_username} (ID: {bot_id})")
                oauth2_url = generate_oauth2_url(bot_id)
                print("-" * 70)
                print("[連結] 您的專屬機器人邀請連結 (已具備完整權限):")
                print(f"   {oauth2_url}")
                print("[提示] 請在瀏覽器中開啟上方網址，將機器人邀請至您的 Discord 伺服器！")
                print("-" * 70 + "\n")
                break
            else:
                print(f"[錯誤] 驗證失敗: {err}")
                print("請檢查 Token 是否複製完整，或確認網路連線正常。\n")

    # -------------------------------------------------------------
    # Step: Telegram Configuration (if selected)
    # -------------------------------------------------------------
    if enable_telegram:
        print("\n" + "-" * 70)
        print("【步驟】配置 Telegram 平台")
        print("  1. 在 Telegram 搜尋官方帳號 @BotFather (https://t.me/BotFather)")
        print("  2. 發送 /newbot 指令，並依提示輸入機器人顯示名稱與 username (需以 bot 結尾)")
        print("  3. 複製 BotFather 發送之 HTTP API Token (格式如 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ)\n")

        while True:
            try:
                tg_token_in = input("請輸入 Telegram Bot Token (必填，或輸入 q 退出): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n\n[精靈已取消] 設定未完成。")
                return False

            if not tg_token_in:
                print("[錯誤] 輸入不能為空，請重新輸入。\n")
                continue
            if tg_token_in.lower() in ("q", "quit", "exit"):
                print("[精靈已取消] 設定未完成。")
                return False

            print("[驗證中] 正在向 Telegram API 驗證 Token (/getMe)...")
            valid, t_id, t_user, err = validate_telegram_token(tg_token_in)
            if valid:
                telegram_bot_token = tg_token_in
                tg_user_str = t_user or "Telegram Bot"
                print(f"[成功] Telegram Token 驗證成功！機器人名稱: @{tg_user_str} (ID: {t_id})")
                print("-" * 70)
                print(f"[提示] 您可在 Telegram 搜尋 @{tg_user_str} 並發送 /start 啟用個人推播。")
                print("-" * 70 + "\n")
                break
            else:
                print(f"[錯誤] 驗證失敗: {err}")
                print("請檢查 Token 是否複製完整，或確認網路連線正常。\n")

        try:
            tg_chat_in = input("請輸入預設接收推播的 Telegram Chat ID (選填，直接按 Enter 略過): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n[精靈已取消] 設定未完成。")
            return False

        if tg_chat_in.lower() in ("q", "quit", "exit"):
            print("[精靈已取消] 設定未完成。")
            return False
        telegram_chat_id = tg_chat_in

    # -------------------------------------------------------------
    # Step: Email (SMTP) Configuration (if selected)
    # -------------------------------------------------------------
    if enable_email:
        print("\n" + "-" * 70)
        print("【步驟】配置 Email (SMTP) 郵件推播平台")
        print("  本功能將在 PTT 監控到符合關鍵字之文章時，以 SMTP 發送美觀之 HTML 郵件至指定信箱。")
        print("  常用設定：Gmail 請至 Google 帳戶 -> 安全性 -> 兩步驟驗證 ->「應用程式密碼」產生 16 碼專屬密碼。\n")

        try:
            host_in = input("請輸入 SMTP 伺服器主機名稱 [smtp.gmail.com]: ").strip()
            if host_in.lower() in ("q", "quit", "exit"):
                print("[精靈已取消] 設定未完成。")
                return False
            smtp_host = host_in or "smtp.gmail.com"

            port_in = input("請輸入 SMTP 連線連接埠 [587]: ").strip()
            if port_in.lower() in ("q", "quit", "exit"):
                print("[精靈已取消] 設定未完成。")
                return False
            try:
                smtp_port = int(port_in) if port_in else 587
            except ValueError:
                smtp_port = 587

            user_in = input("請輸入 SMTP 帳號 / 發信信箱 (例如 your_account@gmail.com): ").strip()
            if user_in.lower() in ("q", "quit", "exit"):
                print("[精靈已取消] 設定未完成。")
                return False
            smtp_user = user_in

            pass_in = input("請輸入 SMTP 應用程式密碼 (16 碼 App Password): ").strip()
            if pass_in.lower() in ("q", "quit", "exit"):
                print("[精靈已取消] 設定未完成。")
                return False
            smtp_password = pass_in

            tls_in = input("是否啟用 STARTTLS 安全加密連線 (y/n) [y]: ").strip().lower()
            if tls_in in ("q", "quit", "exit"):
                print("[精靈已取消] 設定未完成。")
                return False
            smtp_use_tls = False if tls_in in ("n", "no", "false", "0") else True

            print("[驗證中] 正在測試 SMTP 連線與身分認證...")
            valid, err = validate_smtp_credentials(
                host=smtp_host,
                port=smtp_port,
                user=smtp_user,
                password=smtp_password,
                use_tls=smtp_use_tls,
            )
            if valid:
                print("[成功] SMTP 伺服器連線與憑證驗證成功！\n")
            else:
                print(f"[警告] SMTP 驗證失敗: {err}\n")

            to_in = input("請輸入預設接收推播通知之目標 Email (支援逗號分隔多組地址): ").strip()
            if to_in.lower() in ("q", "quit", "exit"):
                print("[精靈已取消] 設定未完成。")
                return False
            email_to = to_in

        except (KeyboardInterrupt, EOFError):
            print("\n\n[精靈已取消] 設定未完成。")
            return False

    # -------------------------------------------------------------
    # Step: Groq API Key (Optional)
    # -------------------------------------------------------------
    print("\n" + "-" * 70)
    print("【步驟】配置 Groq Cloud API Key (選填)")
    print("  Groq 提供超高速 AI 推論服務，用於自然語言追蹤與文章智慧審核。")
    print("  若您尚未申請，可前往免費獲取: https://console.groq.com/keys")
    print("  [提示] 若暫時不使用 AI 功能，可直接按 [Enter] 略過此步驟。\n")

    while True:
        try:
            user_input = input(
                "請輸入 Groq API Key (以 gsk_ 開頭，直接按 Enter 略過): "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n[精靈已取消] 設定未完成。")
            return False

        if not user_input:
            print("[資訊] 已略過 Groq API Key 設定（後續仍可隨時在 .env 中補充）。\n")
            groq_api_key = ""
            break

        print("[驗證中] 正在向 Groq API 驗證 API Key...")
        valid, err = validate_groq_api_key(user_input)
        if valid:
            groq_api_key = user_input
            print("[成功] Groq API Key 驗證成功！\n")
            break
        else:
            print(f"[錯誤] 驗證失敗: {err}")
            print("請確認金鑰是否正確，或直接按 [Enter] 略過此步驟。\n")

    # -------------------------------------------------------------
    # Step: Default PTT Board
    # -------------------------------------------------------------
    print("【步驟】設定預設監控看板")
    print("  常見看板推薦: HardwareSale (電蝦), MacShop, nb-shopping, Gamesale, MobileComm")
    print(f"  [提示] 直接按 [Enter] 將採用預設值: {DEFAULT_BOARD_NAME}\n")

    while True:
        try:
            user_input = input(
                f"請輸入預設看板名稱 [{DEFAULT_BOARD_NAME}]: "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n[精靈已取消] 設定未完成。")
            return False

        if not user_input:
            default_board = DEFAULT_BOARD_NAME
            print(f"[成功] 使用預設看板: {default_board}\n")
            break

        if is_valid_board_name(user_input):
            default_board = user_input
            print(f"[成功] 設定看板: {default_board}\n")
            break
        else:
            print("[錯誤] 看板名稱格式不正確（僅限英數字、底線與連字號），請重新輸入。\n")

    # -------------------------------------------------------------
    # Step: Bot Display Language
    # -------------------------------------------------------------
    print("【步驟】設定機器人顯示語言 (Display Language)")
    print("  1. 繁體中文 (zh_TW) [預設]")
    print("  2. English (en)")
    print(f"  [提示] 直接按 [Enter] 將採用預設值: 1. 繁體中文 ({DEFAULT_LANGUAGE})\n")

    while True:
        try:
            user_input = input("請選擇顯示語言編號 [1]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n[精靈已取消] 設定未完成。")
            return False
        except StopIteration:
            # Backward-compatibility guard for test mock input iterators
            bot_language = DEFAULT_LANGUAGE
            break

        if not user_input:
            bot_language = DEFAULT_LANGUAGE
            print(f"[成功] 使用預設語言: 繁體中文 ({bot_language})\n")
            break

        if user_input.lower() in ("q", "quit", "exit"):
            print("[精靈已取消] 設定未完成。")
            return False

        normalized = SUPPORTED_LANGUAGES.get(user_input.lower())
        if normalized:
            bot_language = normalized
            lang_label = "繁體中文 (zh_TW)" if bot_language == "zh_TW" else "English (en)"
            print(f"[成功] 設定語言: {lang_label}\n")
            break
        else:
            bot_language = DEFAULT_LANGUAGE
            print(f"[警告] 輸入無效，採用預設語言: 繁體中文 ({bot_language})\n")
            break

    # -------------------------------------------------------------
    # Final: Save to .env
    # -------------------------------------------------------------
    print("[儲存] 正在儲存設定至 .env 檔案...")
    success = write_env_file(
        env_path=env_path,
        discord_token=discord_token,
        groq_api_key=groq_api_key,
        default_board=default_board,
        bot_language=bot_language,
        enable_discord=enable_discord,
        enable_telegram=enable_telegram,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        enable_email=enable_email,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        smtp_use_tls=smtp_use_tls,
        email_to=email_to,
    )

    if success:
        print("=" * 70)
        print("[完成] 設定完成！.env 檔案已成功建立。")
        if os.name != "nt":
            print("[安全] 已套用安全檔案權限 (0600)。")
        print("=" * 70)
        print("\n已啟用的通知平台：")
        if enable_discord:
            print("  - Discord: 已啟用")
        if enable_telegram:
            print("  - Telegram: 已啟用")
        if enable_email:
            print(f"  - Email: 已啟用 (目標: {email_to or '未設定'})")
        print("\n後續啟動方式：")
        print("  - 命令列啟動: python3 bot.py")
        print("  - Windows: 雙擊 start_windows.bat")
        print("  - macOS: 雙擊 start_mac.command")
        if enable_discord:
            print("  - 入群後在頻道輸入 /setup 開啟視覺化設定面板！\n")
        else:
            print()
        return True
    else:
        print("[錯誤] 儲存 .env 失敗，請確認磁碟寫入權限。\n")
        return False


if __name__ == "__main__":
    try:
        ok = run_wizard()
        sys.exit(0 if ok else 1)
    except KeyboardInterrupt:
        print("\n[精靈已中斷]")
        sys.exit(1)
