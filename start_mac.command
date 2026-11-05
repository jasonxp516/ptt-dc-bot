#!/usr/bin/env bash
# =============================================================================
# PTT-DC-Bot macOS 雙擊啟動器 (start_mac.command)
# 雙擊即可在 macOS Terminal 中自動建置環境、引導設定並啟動機器人
# =============================================================================

# 1. 切換至腳本所在目錄 (macOS Finder 雙擊預設在 $HOME)
cd "$(dirname "$0")" || exit 1

echo "============================================================"
echo "          PTT-DC-Bot macOS 啟動器                           "
echo "============================================================"

# 2. 檢查 Python 3.10+ 環境
if ! command -v python3 >/dev/null 2>&1; then
    echo ""
    echo "[錯誤] macOS 系統未安裝 python3。"
    echo "請先安裝 Python 3.10 以上版本 (建議透過 Homebrew 安裝: brew install python3)"
    echo "或至官網下載安裝: https://www.python.org/downloads/macos/"
    echo ""
    read -p "請按 Enter 鍵關閉此視窗..."
    exit 1
fi

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    PY_VER="$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>/dev/null || echo "未知")"
    echo ""
    echo "[錯誤] Python 版本過低 (當前版本: $PY_VER)。PTT-DC-Bot 需要 Python 3.10 以上版本。"
    echo "請升級 Python 後再重新啟動 (建議: brew install python3)"
    echo ""
    read -p "請按 Enter 鍵關閉此視窗..."
    exit 1
fi

# 3. 自動檢查與建立虛擬環境 (venv)
if [ ! -d "venv" ] || [ ! -x "venv/bin/python" ]; then
    echo "[資訊] 首次運行，正在建立 Python 獨立虛擬環境 (venv)..."
    python3 -m venv venv || {
        echo ""
        echo "[錯誤] 建立虛擬環境失敗。"
        read -p "請按 Enter 鍵關閉此視窗..."
        exit 1
    }
    echo "[資訊] 正在安裝相依套件 (requirements.txt)..."
    ./venv/bin/pip install --upgrade pip --quiet
    ./venv/bin/pip install -r requirements.txt || {
        echo ""
        echo "[錯誤] 相依套件安裝失敗，請檢查網路連線。"
        read -p "請按 Enter 鍵關閉此視窗..."
        exit 1
    }
fi

# 4. 首次使用設定精靈檢查 (.env)
NEEDS_WIZARD=0
if [ ! -f ".env" ]; then
    NEEDS_WIZARD=1
elif ! grep -qE '^DISCORD_TOKEN=[^#[:space:]]+' .env 2>/dev/null; then
    NEEDS_WIZARD=1
fi

if [ "$NEEDS_WIZARD" -eq 1 ]; then
    echo ""
    echo "============================================================"
    echo " 尚未完成機器人設定，正在啟動設定精靈 (setup_wizard.py)..."
    echo "============================================================"
    ./venv/bin/python setup_wizard.py
    WIZARD_STATUS=$?
    if [ "$WIZARD_STATUS" -ne 0 ]; then
        echo ""
        echo "[提示] 設定精靈未完成。"
        read -p "請按 Enter 鍵關閉此視窗..."
        exit "$WIZARD_STATUS"
    fi
fi

# 5. 啟動機器人主程式
echo ""
echo "============================================================"
echo " 正在啟動 PTT Discord Bot..."
echo "============================================================"
./venv/bin/python bot.py
EXIT_CODE=$?

# 6. 程序結束後保持終端機視窗開啟，防止 Finder 直接閃退關閉
echo ""
echo "============================================================"
echo "機器人已結束運行 (退出代碼: $EXIT_CODE)。"
read -p "請按 Enter 鍵關閉此視窗..."
exit "$EXIT_CODE"
