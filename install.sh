#!/usr/bin/env bash
# =============================================================================
# PTT-DC-Bot 一鍵安裝與設定腳本 (Linux / macOS)
# 支援一行指令安裝：curl -fsSL https://raw.githubusercontent.com/.../install.sh | bash
# =============================================================================

{
set -e

# 1. 處理管線模式 (curl ... | bash) 之 stdin 終端重定向
if (exec < /dev/tty) 2>/dev/null; then
    exec < /dev/tty
fi

echo "============================================================"
echo "          PTT-DC-Bot 跨平台自動安裝與設定精靈               "
echo "============================================================"

# 2. 判斷執行目錄與專案下載
if [ -f "bot.py" ] && [ -f "requirements.txt" ]; then
    PROJECT_DIR="$(pwd)"
    echo "[資訊] 偵測到當前目錄為 PTT-DC-Bot 專案目錄: $PROJECT_DIR"
else
    PROJECT_DIR="$HOME/ptt-dc-bot"
    echo "[資訊] 目標安裝目錄: $PROJECT_DIR"
    if [ ! -d "$PROJECT_DIR" ]; then
        echo "[資訊] 正在取得 PTT-DC-Bot 最新程式碼..."
        if command -v git >/dev/null 2>&1; then
            git clone https://github.com/jasonxp516/ptt-dc-bot.git "$PROJECT_DIR"
        else
            mkdir -p "$PROJECT_DIR"
            echo "[資訊] 未安裝 git，改透過 tarball 下載專案..."
            # 優先下載最新正式版；查詢失敗時退回 main 分支
            LATEST_TAG="$(curl -fsSL https://api.github.com/repos/jasonxp516/ptt-dc-bot/releases/latest 2>/dev/null | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
            if [ -n "$LATEST_TAG" ]; then
                echo "[資訊] 下載最新正式版: $LATEST_TAG"
                TARBALL_URL="https://github.com/jasonxp516/ptt-dc-bot/archive/refs/tags/${LATEST_TAG}.tar.gz"
            else
                echo "[提示] 無法查詢最新正式版，改下載 main 分支"
                TARBALL_URL="https://github.com/jasonxp516/ptt-dc-bot/archive/refs/heads/main.tar.gz"
            fi
            curl -fsSL "$TARBALL_URL" | tar -xz -C "$PROJECT_DIR" --strip-components=1
        fi
    else
        echo "[資訊] 目標目錄已存在，直接在此目錄更新與安裝..."
    fi
    cd "$PROJECT_DIR" || {
        echo "[錯誤] 無法切換至專案目錄: $PROJECT_DIR"
        exit 1
    }
fi

# 3. 檢查 Python 3.10+ 環境
echo "[資訊] 檢查 Python 3 環境..."
if ! command -v python3 >/dev/null 2>&1; then
    echo ""
    echo "[錯誤] 系統未安裝 python3。PTT-DC-Bot 需要 Python 3.10 以上版本。"
    echo "請依您的系統安裝 Python 3.10+："
    echo "  - Ubuntu / Debian: sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip"
    echo "  - Fedora / RHEL:   sudo dnf install -y python3 python3-pip"
    echo "  - macOS (Homebrew): brew install python3"
    exit 1
fi

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    PY_VER="$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>/dev/null || echo "未知")"
    echo ""
    echo "[錯誤] Python 版本過低 (當前版本: $PY_VER)。PTT-DC-Bot 需要 Python 3.10 以上版本。"
    echo "請升級系統 Python 版本後再重新執行。"
    exit 1
fi

# 4. 建立 Python 虛擬環境 (venv)
if [ ! -d "venv" ] || [ ! -x "venv/bin/python" ]; then
    echo "[資訊] 正在建立 Python 獨立虛擬環境 (python3 -m venv venv)..."
    if ! python3 -m venv venv; then
        echo ""
        echo "[錯誤] 建立虛擬環境失敗。"
        echo "若為 Ubuntu/Debian 系統，通常需要額外安裝 python3-venv："
        echo "  sudo apt-get update && sudo apt-get install -y python3-venv"
        exit 1
    fi
fi

VENV_PYTHON="./venv/bin/python"
VENV_PIP="./venv/bin/pip"

# 5. 安裝相依套件 (requirements.txt)
echo "[資訊] 正在安裝相依套件 (pip install -r requirements.txt)..."
"$VENV_PIP" install --upgrade pip --quiet
if ! "$VENV_PIP" install -r requirements.txt; then
    echo ""
    echo "[錯誤] 相依套件安裝失敗，請檢查網路連線或套件相依性後重試。"
    exit 1
fi

# 6. 設定各腳本執行權限 (chmod +x)
echo "[資訊] 設定腳本可執行權限 (chmod +x)..."
chmod +x install.sh start_mac.command start_bg.sh stop_bg.sh restart_bg.sh 2>/dev/null || true

# 7. 檢查是否需要執行設定精靈 (setup_wizard.py)
NEEDS_WIZARD=0
if [ ! -f ".env" ]; then
    NEEDS_WIZARD=1
elif ! grep -qE '^DISCORD_TOKEN=[^#[:space:]]+' .env 2>/dev/null; then
    NEEDS_WIZARD=1
fi

if [ "$NEEDS_WIZARD" -eq 1 ]; then
    if [ -t 0 ]; then
        echo ""
        echo "============================================================"
        echo " 尚未設定憑證，正在啟動首次設定精靈 (setup_wizard.py)..."
        echo "============================================================"
        "$VENV_PYTHON" setup_wizard.py
    else
        echo ""
        echo "[提示] 偵測到非互動式環境，已跳過設定精靈。"
        echo "請手動建立 .env 檔案並填寫 DISCORD_TOKEN："
        echo "  cp .env.example .env && nano .env"
    fi
fi

# 8. 完成提示與啟動選項
echo ""
echo "============================================================"
echo "                  PTT-DC-Bot 安裝完成！                     "
echo "============================================================"

if [ -t 0 ]; then
    echo "請選擇機器人啟動方式："
    echo "  1) 前景立即啟動 (Foreground: ./venv/bin/python bot.py)"
    if command -v tmux >/dev/null 2>&1; then
        echo "  2) 背景常駐啟動 (tmux: bash start_bg.sh)"
    fi
    echo "  3) 稍後自行啟動 (結束此安裝程式)"
    read -r -p "請輸入選項 [1/2/3] (預設: 1): " LAUNCH_CHOICE || LAUNCH_CHOICE="1"
    LAUNCH_CHOICE="${LAUNCH_CHOICE:-1}"

    case "$LAUNCH_CHOICE" in
        2)
            if command -v tmux >/dev/null 2>&1; then
                echo "[資訊] 正在以 tmux 背景模式啟動..."
                bash start_bg.sh
            else
                echo "[提示] 系統未安裝 tmux，改以前景模式啟動..."
                "$VENV_PYTHON" bot.py
            fi
            ;;
        3)
            echo "安裝已順利完成。日後可使用下列指令啟動："
            echo "  前景啟動: ./venv/bin/python bot.py"
            echo "  背景常駐: bash start_bg.sh"
            ;;
        *)
            echo "[資訊] 正在以前景模式啟動 PTT-DC-Bot..."
            "$VENV_PYTHON" bot.py
            ;;
    esac
else
    echo "安裝已順利完成。日後可使用下列指令啟動："
    echo "  前景啟動: ./venv/bin/python bot.py"
    echo "  背景常駐: bash start_bg.sh"
fi

exit 0
}
