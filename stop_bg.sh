#!/usr/bin/env bash
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="ptt_bot"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    tmux kill-session -t "$SESSION_NAME"
    sleep 0.5
    echo "已停止 tmux ($SESSION_NAME) 中的機器人服務。"
else
    echo "目前沒有執行中的 tmux ($SESSION_NAME) 服務。"
fi
pkill -f "$PROJECT_DIR/bot.py" 2>/dev/null || true
