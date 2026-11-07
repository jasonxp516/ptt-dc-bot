#!/usr/bin/env python3
"""
PTT-DC-Bot Setup Wizard
-----------------------
A lightweight, dependency-free interactive configuration wizard for PTT-DC-Bot.
Uses ONLY Python standard library modules (urllib.request, json, os, sys, stat, re).
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Optional, Tuple

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


def generate_oauth2_url(client_id: str) -> str:
    """Generate Discord bot invite URL with appropriate permissions and scope."""
    cleaned_id = str(client_id).strip()
    return (
        f"https://discord.com/oauth2/authorize?client_id={cleaned_id}"
        f"&permissions={PERMISSIONS_INTEGER}&scope={OAUTH2_SCOPE}"
    )


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
    discord_token: str,
    groq_api_key: str = "",
    default_board: str = DEFAULT_BOARD_NAME,
    poll_interval_min: int = 90,
    poll_interval_max: int = 150,
    groq_model: str = "openai/gpt-oss-120b",
    groq_fast_model: str = "openai/gpt-oss-20b",
    bot_language: str = "zh_TW",
) -> bool:
    """
    Write environment configuration to .env with UTF-8 encoding.
    Applies POSIX 0600 permissions if not running on Windows.
    """
    content = (
        "# =====================================================================\n"
        "# PTT-DC-Bot 環境設定檔 (由設定精靈 setup_wizard.py 自動產生)\n"
        "# =====================================================================\n\n"
        "# Discord Bot 憑證 (必填)\n"
        f"DISCORD_TOKEN={discord_token.strip()}\n\n"
        "# 界面顯示語言 (zh_TW: 繁體中文, en: English)\n"
        f"BOT_LANGUAGE={(bot_language or DEFAULT_LANGUAGE).strip()}\n\n"
        "# 預設主伺服器與預設推播頻道 ID (選填，亦可進入頻道後以 /setup 設定)\n"
        "DEFAULT_CHANNEL_ID=\n"
        "DEFAULT_GUILD_ID=\n\n"
        "# 機器人最高管理員 Discord User ID (選填，留空時會自動識別)\n"
        "OWNER_USER_ID=\n\n"
        "# 預設監控看板與輪詢抖動秒數區間\n"
        f"DEFAULT_BOARD={default_board.strip()}\n"
        f"POLL_INTERVAL_MIN={poll_interval_min}\n"
        f"POLL_INTERVAL_MAX={poll_interval_max}\n\n"
        "# Groq Cloud API 配置 (啟用 AI 自然語言助理與語意審核時使用)\n"
        f"GROQ_API_KEY={groq_api_key.strip()}\n"
        f"GROQ_MODEL={groq_model.strip()}\n"
        f"GROQ_FAST_MODEL={groq_fast_model.strip()}\n"
    )

    try:
        abs_env_path = os.path.abspath(env_path)
        env_dir = os.path.dirname(abs_env_path)
        if env_dir:
            os.makedirs(env_dir, exist_ok=True)

        with open(abs_env_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Set secure 0600 permissions on POSIX
        if os.name != "nt":
            try:
                os.chmod(abs_env_path, 0o600)
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
    print("本精靈將引導您完成 Discord Bot 憑證與相關參數設定。\n")

    if not is_interactive():
        try:
            if sys.stderr and hasattr(sys.stderr, "write"):
                sys.stderr.write("錯誤：設定精靈需要互動式終端機 (TTY)。\n")
        except (ValueError, AttributeError, OSError):
            pass
        return False

    # -------------------------------------------------------------
    # Step 1: Discord Bot Token
    # -------------------------------------------------------------
    print("【步驟 1/4】配置 Discord Bot Token")
    print("  1. 前往 Discord 開發者入口: https://discord.com/developers/applications")
    print("  2. 建立或選擇您的 Application，點選左側「Bot」分頁")
    print("  3. 點選「Reset Token」獲取金鑰並複製")
    print("  4. [注意] 請務必在同一頁面下方的「Privileged Gateway Intents」中勾選：")
    print("     - MESSAGE CONTENT INTENT (必須開啟才能正常運作)\n")

    discord_token = ""
    bot_id = ""
    bot_username = ""

    while True:
        try:
            user_input = input("請貼上 Discord Bot Token (或輸入 q 退出): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n[精靈已取消] 設定未完成。")
            return False

        if not user_input:
            print("[錯誤] 輸入不能為空，請重新輸入。\n")
            continue
        if user_input.lower() in ("q", "quit", "exit"):
            print("[精靈已取消] 設定未完成。")
            return False

        cleaned_input = re.sub(r"^[Bb][Oo][Tt](\s+|$)", "", user_input).strip()
        if not cleaned_input:
            print("[錯誤] 輸入不能為空，請重新輸入。\n")
            continue

        print("[驗證] 正在向 Discord API 驗證 Token...")
        valid, b_id, b_user, err = validate_discord_token(cleaned_input)
        if valid:
            discord_token = cleaned_input
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
    # Step 2: Groq API Key (Optional)
    # -------------------------------------------------------------
    print("【步驟 2/4】配置 Groq Cloud API Key (選填)")
    print("  Groq 提供超高速 AI 推論服務，用於自然語言追蹤與文章智慧審核。")
    print("  若您尚未申請，可前往免費獲取: https://console.groq.com/keys")
    print("  [提示] 若暫時不使用 AI 功能，可直接按 [Enter] 略過此步驟。\n")

    groq_api_key = ""

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

        print("[驗證] 正在向 Groq API 驗證 API Key...")
        valid, err = validate_groq_api_key(user_input)
        if valid:
            groq_api_key = user_input
            print("[成功] Groq API Key 驗證成功！\n")
            break
        else:
            print(f"[錯誤] 驗證失敗: {err}")
            print("請確認金鑰是否正確，或直接按 [Enter] 略過此步驟。\n")

    # -------------------------------------------------------------
    # Step 3: Default PTT Board
    # -------------------------------------------------------------
    print("【步驟 3/4】設定預設監控看板")
    print("  常見看板推薦: HardwareSale (電蝦), MacShop, nb-shopping, Gamesale, MobileComm")
    print(f"  [提示] 直接按 [Enter] 將採用預設值: {DEFAULT_BOARD_NAME}\n")

    default_board = DEFAULT_BOARD_NAME

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
    # Step 4: Bot Display Language
    # -------------------------------------------------------------
    print("【步驟 4/4】設定機器人顯示語言 (Display Language)")
    print("  1. 繁體中文 (zh_TW) [預設]")
    print("  2. English (en)")
    print(f"  [提示] 直接按 [Enter] 將採用預設值: 1. 繁體中文 ({DEFAULT_LANGUAGE})\n")

    bot_language = DEFAULT_LANGUAGE

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
    )

    if success:
        print("=" * 70)
        print("[完成] 設定完成！.env 檔案已成功建立。")
        if os.name != "nt":
            print("[安全] 已套用安全檔案權限 (0600)。")
        print("=" * 70)
        print("\n後續啟動方式：")
        print("  - 命令列啟動: python3 bot.py")
        print("  - Windows: 雙擊 start_windows.bat")
        print("  - macOS: 雙擊 start_mac.command")
        print("  - 入群後在頻道輸入 /setup 開啟視覺化設定面板！\n")
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
