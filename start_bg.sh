#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

SESSION_NAME="ptt_bot"
PYTHON_BIN="$PROJECT_DIR/venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "機器人已在 tmux ($SESSION_NAME) 背景執行中！"
    exit 0
fi

tmux new-session -d -s "$SESSION_NAME" "cd \"$PROJECT_DIR\" && while true; do if [ -f \"$PROJECT_DIR/tmux_console.log\" ]; then tail -n 1000 \"$PROJECT_DIR/tmux_console.log\" > \"$PROJECT_DIR/tmux_console.log.tmp\" && mv \"$PROJECT_DIR/tmux_console.log.tmp\" \"$PROJECT_DIR/tmux_console.log\"; fi; \"$PYTHON_BIN\" \"$PROJECT_DIR/bot.py\" 2>> \"$PROJECT_DIR/tmux_console.log\"; echo '機器人異常退出，10秒後自動重啟...' >> \"$PROJECT_DIR/tmux_console.log\"; sleep 10; done"
echo "已在 tmux ($SESSION_NAME) 啟動機器人背景常駐服務（關閉 SSH 也不會中斷）！"
