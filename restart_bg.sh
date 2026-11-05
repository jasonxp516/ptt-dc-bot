#!/usr/bin/env bash
set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "$PROJECT_DIR/stop_bg.sh" || true
sleep 1
bash "$PROJECT_DIR/start_bg.sh"
