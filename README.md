# PTT-DC-Bot

[English](README_EN.md) | **繁體中文**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![Groq API](https://img.shields.io/badge/LLM-Groq%20API-f55036)](https://groq.com/)
[![Docker Image](https://img.shields.io/badge/Docker-GHCR%20Multi--Arch-2496ED?logo=docker&logoColor=white)](https://github.com/jasonxp516/ptt-dc-bot/pkgs/container/ptt-dc-bot)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

結合規則過濾與 Groq LLM 語意判讀的 PTT 看板監控與多平台推播機器人，支援
Discord、Telegram 與 Email SMTP。它會判斷已售出狀態、拆分合售文品項、排除硬體
型號造成的價格誤判，並以 SQLite WAL 支援伺服器公用頻道與個人私訊的多租戶規則。

## 核心功能

- 已售出追蹤：持續檢查作者最新推文，並在 Discord/Telegram 原位更新通知。
- 多品項解析：同一篇合售文章可依關鍵字分別判斷價格與售出狀態。
- 精確關鍵字與 AI 模糊規則：支援 AND、排除詞、預算上限與自然語言條件。
- Crawl-Once, Fan-Out：每輪每個看板只抓一次，共享文章與 AI 快取，再分發給訂閱者。
- `/setup` 視覺化設定面板、Slash 指令、Telegram 長輪詢與 Email HTML 通知。

## 快速開始

先準備至少一種推播管道的憑證：

1. Discord：到 [Developer Portal](https://discord.com/developers/applications) 建立
   Application 並取得 Bot Token。
2. Telegram：由 [@BotFather](https://t.me/BotFather) 建立 Bot 並取得 Chat ID。
3. Email：準備 SMTP 帳號；Gmail 建議使用應用程式密碼。
4. Groq（選用）：從 [Groq Console](https://console.groq.com/keys) 取得 API Key，
   啟用自然語言對話與 AI 語意審核。

### Discord 重要說明

**不需要啟用 Privileged Gateway Intents，也不需要啟用 Message Content Intent。**
Discord 對 Bot 的私訊、明確 `@Bot` 提及與 Slash commands 原生提供訊息內容；只要
授予邀請連結所需權限並完成同步即可。請妥善保管 Token。

### 一行安裝

```bash
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.ps1 | iex
```

### Releases

從 [GitHub Releases](https://github.com/jasonxp516/ptt-dc-bot/releases) 下載對應平台
壓縮包。Linux 可執行 `./install.sh` 初始化，或使用 `python3 bot.py` 前景啟動。

### Docker Compose / GHCR

官方提供 `ghcr.io/jasonxp516/ptt-dc-bot:latest` AMD64/ARM64 多架構映像檔。

- [Docker Compose 與 GHCR 完整指南](docs/deploy-docker.md)
- [Synology DSM Container Manager 指南](docs/deploy-synology.md)
- [unRAID Docker Template 指南](docs/deploy-unraid.md)
- [Linux systemd、tmux 與 daemon 腳本指南](docs/deploy-linux.md)
- [隱私政策與資料刪除](PRIVACY.md)

最小 Compose 操作：

```bash
mkdir -p ptt-dc-bot/data && cd ptt-dc-bot
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/docker-compose.yml -o docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/.env.example -o .env
chmod 600 .env
# 編輯 .env 填入 Token/Key
docker compose pull && docker compose up -d
docker compose logs -f
```

Compose 會持久化 `./data`、採用最小權限與資源限制，並透過 heartbeat 健康檢查
確認 SQLite 與最近 600 秒內更新的 heartbeat 都存在。

## 平台設定

### Discord

邀請 Bot 時勾選 `bot` 與 `applications.commands`，再使用 `/setup` 選擇看板、
綁定頻道並新增關鍵字。伺服器共用設定需要管理權限；個人規則可直接私訊 Bot。

<p align="center">
  <img src="assets/setup_control_panel.svg" alt="PTT-DC-Bot /setup 視覺化互動設定面板" width="680"/>
</p>
<p align="center">
  <img src="assets/dropdown_boards_select.svg" alt="快速選擇監控看板下拉選單" width="680"/>
</p>
<p align="center">
  <img src="assets/keyword_modal_form.svg" alt="新增追蹤關鍵字彈出表單" width="560"/>
</p>
<p align="center">
  <img src="assets/notification_embed_preview.svg" alt="即時推播通知 Embed 卡片預覽" width="680"/>
</p>

### Telegram / Email

在 `.env` 啟用對應平台並填入 Token、Chat ID 或 SMTP 參數；Bot 啟動後可用
`/keyword_add`、`/fuzzy_add`、`/channel_set` 等指令管理規則與通知目標。

## 常用指令

| 指令 | 用途 |
|---|---|
| `/setup` | 開啟視覺化設定面板 |
| `/ai` | 以自然語言建立規則、查詢看板或搜尋現貨 |
| `/keyword_add` | 新增精確關鍵字與預算上限 |
| `/fuzzy_add` | 新增 AI 模糊語意規則 |
| `/keyword_list` / `/fuzzy_list` | 查看目前規則 |
| `/scan_unsold` | 回溯搜尋尚未售出的文章 |
| `/check_now` | 立即巡檢（有冷卻時間） |
| `/pause` / `/resume` | 暫停或恢復通知 |
| `/forget_me` | 永久刪除個人規則、推播紀錄與對話記憶 |

## 環境變數

常用設定如下；完整範例請參考 `.env.example` 與 Docker 指南。

| 變數 | 說明 |
|---|---|
| `DISCORD_TOKEN` | Discord Bot Token |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram 憑證（選用） |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` / `EMAIL_TO` | Email 憑證（選用） |
| `GROQ_API_KEY` | Groq 語意審核與自然語言對話（選用） |
| `DEFAULT_BOARD` | 預設看板，預設 `HardwareSale` |
| `POLL_INTERVAL_MIN` / `POLL_INTERVAL_MAX` | 巡檢間隔秒數 |
| `DB_PATH` / `LOG_PATH` | SQLite 與日誌路徑 |

## 架構與目錄

```text
ptt-dc-bot/
├── bot.py                  # Discord Client、Slash commands、輪詢協調器
├── telegram_bot.py         # Telegram 長輪詢與指令
├── email_notifier.py       # Email SMTP 通知
├── delivery.py             # 多平台派發
├── sold_rules.py           # 已售出判定規則
├── boards.py               # 看板驗證與別名
├── boards.json             # 看板清單
├── timeutil.py             # 台北時區時間工具
├── setup_wizard.py         # 互動式設定精靈
├── config.py               # 環境變數與輸入驗證
├── storage.py              # SQLite WAL 多租戶資料庫（Schema v5）
├── ptt_crawler.py          # PTT 非同步爬蟲與品項切分
├── groq_scheduler.py       # Groq 優先級佇列與模型輪替
├── groq_agent.py           # ReAct AI 代理與工具權限
├── embeds.py               # Discord Embed 與分頁
├── docs/                   # 部署與可行性文件
├── PRIVACY.md              # 資料處理與刪除權利
├── Dockerfile              # 非 root Python 映像檔
├── docker-compose.yml      # GHCR 多架構編排與健康檢查
├── .github/workflows/      # CI/CD 與發布流程
└── tests/                  # 離線單元與回歸測試
```

背景流程會聚合活躍看板、共享抓取與 AI 快取、依關鍵字建立虛擬品項視角，再將
結果派發至各訂閱者；推播後會追蹤 18 小時內的售出更新。

## 部署後檢查

### Docker

```bash
docker compose ps
docker inspect --format='{{json .State.Health}}' ptt-dc-bot
docker compose logs --tail=100 ptt-dc-bot
```

健康狀態需要同時滿足：

1. `/app/data/bot_data.db` 存在。
2. `/app/data/heartbeat` 存在。
3. heartbeat 檔案在 600 秒內曾更新。

若容器顯示 `unhealthy`，先檢查 `data` 掛載目錄的寫入權限、Token 與主機時間，
不要在排錯時刪除資料庫。

### Linux 原生服務

systemd 主機可用：

```bash
sudo systemctl status ptt-bot
journalctl -u ptt-bot -f
```

tmux 部署可用：

```bash
bash start_bg.sh
tmux attach -t ptt_bot
# Ctrl-b d 離開 tmux
bash stop_bg.sh
```

systemd 與 tmux 請擇一使用，避免同時啟動兩個 Bot 實例。

## 規則與推播行為

精確關鍵字以空白表示 AND，例如 `Taipei 3060Ti`；以 `-` 開頭的詞表示排除，
例如 `3060 -laptop`。`max_price` 會排除超出預算的文章，並忽略 RTX、i5、記憶體
容量等硬體型號數字。`/fuzzy_add` 適合保固、配件、交易地點等需要語意判讀的條件。

新增看板時系統會建立基準線，避免把歷史文章一次洗版。背景巡檢只抓取有活躍規則
的看板；文章內容快取 10 分鐘，AI 判定快取 60 分鐘。相同文章分發給多個訂閱者
時共用抓取結果，不會為每位使用者重複請求 PTT。

推播後 18 小時內會持續檢查文章狀態。若作者簽名檔後新增「已售出」、「暫售」或
「洽中」等訊息，系統會更新原通知而不是重複發送。合售文則依每個關鍵字建立
獨立的虛擬品項視角。

## 資料、安全與權限

SQLite 使用 WAL 與多租戶 subscriber 隔離。伺服器頻道的共用規則需要適當的
Discord 管理權限；個人規則與通知目標綁定使用者。資料庫目前使用 Schema v5
遷移格式，請透過持久化 `DB_PATH` 保存資料。

PTT 文章只從官方 HTTPS 路徑讀取。若設定 `GROQ_API_KEY`，文章內容與自然語言
對話會送至 Groq 進行語意過濾或聊天；未設定 Key 時不會呼叫 Groq。SQLite 儲存
的欄位與刪除權利詳見 [PRIVACY.md](PRIVACY.md)。

執行 `/forget_me` 會立即且永久刪除該使用者的個人規則、推播紀錄與對話記憶。
這不會刪除其他訂閱者的伺服器共用設定，也不會刪除公開 PTT 頁面。

## 常見問題

**為什麼 Bot 收不到私訊？**
確認 Discord 使用者設定允許伺服器成員傳送私人訊息，並直接對 Bot 發送一則
訊息。這個專案不依賴 Message Content Intent；DM、明確提及與 Slash commands
由 Discord 原生提供內容。

**為什麼沒有通知？**
先用 `/me` 確認規則、看板與通知狀態，再用 `/check_now` 測試。檢查 `/status`
與容器/服務日誌；文章可能已售出、超過預算、超過去重時間，或被 AI 語意規則
排除。

**如何更換憑證？**
停止服務後編輯 `.env`，再重啟容器或 systemd/tmux 服務。不要把 Token 寫入
Compose 檔、Git 儲存庫或公開 issue。

## 本地測試

```bash
/tmp/ptt_venv/bin/pytest -q
/tmp/ptt_venv/bin/python tools/eval_sold.py
```

測試不需要真實 Token 或網路連線。若使用系統 Python，請先安裝
`requirements.txt` 與 `pytest`。

## 授權

本專案採 MIT License，詳見 [LICENSE](LICENSE)。
