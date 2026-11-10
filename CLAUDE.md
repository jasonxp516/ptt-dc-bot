# CLAUDE.md - PTT-DC-Bot 維護審查與交接指南 (Project Handoff Guide)

本文件專為 **Claude Code** 及協同維護者量身編寫，整理專案全貌、登入權限憑證配置、系統架構、核心機制、維運測試指令與重要開發防呆規範。

---

## 1. 專案概況與最新狀態 (Project Overview & Status)

- **專案名稱**：PTT-DC-Bot (PTT 智慧二手交易追蹤與多平台即時推播機器人)
- **最新發行版本**：`v1.1.0` (Production Release)
- **工作目錄**：`/home/jason/Desktop/ptt_dc_bot/ptt_dc_bot_release`
- **Git 遠端倉庫**：`git@github.com:jasonxp516/ptt-dc-bot.git`
- **分支現況**：
  - `main` 分支與 `beta` 分支均已同步至最新提交（包含完整的 Phase 1～4 重構、加固、文件模組化與專案企劃書）。
  - 已建立發布標籤 `v1.1.0` 並推送至遠端，自動觸發 GitHub Actions 釋出跨平台套件與 GHCR 多架構 Docker 映像檔。
- **Python 執行環境**：
  - 主要測試與虛擬環境：`/tmp/ptt_venv`（包含 `pytest`、`aiohttp`、`discord.py`、`pyyaml`、`reportlab` 等）。
  - 若在全新機器，可執行 `python3 -m venv .venv && ./venv/bin/pip install -r requirements.txt pytest pyyaml`。

---

## 2. 登入權限與環境變數憑證配置 (Credentials & Permissions)

所有敏感憑證均透過專案根目錄之 `.env` 檔案載入（權限應設為 `chmod 600 .env`，不可提交至 Git）。
可直接執行 `python3 setup_wizard.py` 透過零依賴終端精靈互動配置，或複製 `.env.example` 自行填寫。

### 2.1 各平台憑證與權限說明

#### A. Discord 平台 (預設啟用 `ENABLE_DISCORD=true`)
- `DISCORD_TOKEN`：Discord Bot 認證金鑰。
  - **重要權限與特權意圖說明**：**完全不需要開啟任何 Privileged Gateway Intents**（包含 `Message Content Intent` **不需要** 開啟）。
  - **原因**：Discord 官方對 Bot 的私訊 (DM)、明確 `@Bot` 提及與 Slash Commands 均原生提供完整訊息內容。
  - **OAuth2 邀請權限**：整數為 `274877975552`（Scopes 勾選 `bot` 與 `applications.commands`；包含 View Channels, Send Messages, Embed Links, Read Message History）。
- `DEFAULT_CHANNEL_ID`：預設伺服器推播頻道 ID（選填，亦可由管理員於頻道執行 `/setup` 綁定）。
- `DEFAULT_GUILD_ID`：主伺服器 ID（選填，享有較高配額階層）。
- `OWNER_USER_ID`：最高管理員 Discord User ID（選填，留空時啟動時自動從主伺服器擁有者辨識）。

#### B. Telegram 平台 (`ENABLE_TELEGRAM=true` 時啟用)
- `TELEGRAM_BOT_TOKEN`：向 Telegram [@BotFather](https://t.me/BotFather) 申請之 Bot Token（格式：`<bot_id>:<secret>`）。
- `TELEGRAM_CHAT_ID`：預設推播目標 Chat ID（個人為正整數例如 `123456789`，群組為負整數例如 `-1001234567890`）。
- `TELEGRAM_ADMIN_USER_ID`：管理員 User ID（選填）。
- **連線架構**：採用出站長輪詢（Long Polling `getUpdates`），**完全免公網 IP、免網域與免 SSL 憑證**，家用網路或防火牆後端皆可正常收發指令。

#### C. Email SMTP 平台 (`ENABLE_EMAIL=true` 時啟用)
- `SMTP_HOST`：SMTP 伺服器主機（如 `smtp.gmail.com`）。
- `SMTP_PORT`：連接埠（STARTTLS 預設 `587`；SSL 預設 `465`）。
- `SMTP_USER`：發信帳號（如 Gmail 信箱）。
- `SMTP_PASSWORD`：**Gmail 必須使用 16 碼「應用程式密碼 (App Password)」**，切勿使用個人登入密碼。
- `SMTP_USE_TLS`：是否啟用 STARTTLS（預設 `true`）。
- `EMAIL_FROM` / `EMAIL_FROM_NAME`：寄件者信箱與顯示名稱。
- `EMAIL_TO`：收件者清單（支援逗號或分號分隔多個收件地址）。

#### D. Groq Cloud API (AI 自然語言與語意審核)
- `GROQ_API_KEY`：格式為 `gsk_...`，至 [Groq Console](https://console.groq.com/keys) 免費申請。
- `GROQ_MODEL`：主力語意分析模型（預設 `openai/gpt-oss-120b`）。
- `GROQ_FAST_MODEL`：輕量初篩模型（預設 `openai/gpt-oss-20b`）。
- **選填特性**：若未填寫此 Key，系統將自動退回純規則與純狀態機模式運作，爬蟲推播、價格過濾與原位劃線更新依然完全正常。

#### E. 核心系統設定
- `BOT_LANGUAGE`：語系偏好（`zh_TW` 繁體中文 或 `en` 英文）。
- `DEFAULT_BOARD`：預設監控看板（預設 `HardwareSale`）。
- `POLL_INTERVAL_MIN` / `POLL_INTERVAL_MAX`：背景巡檢隨機抖動間隔（預設 90～150 秒，避免固定頻率引發限流）。
- `DB_PATH`：SQLite 資料庫路徑（預設 `bot_data.db`，Docker 下為 `/app/data/bot_data.db`）。
- `LOG_PATH`：日誌檔案路徑（預設 `bot.log`，5MB 輪替保留 3 份）。
- `HEARTBEAT_FILE`：心跳檔案路徑（預設為資料庫同目錄下的 `heartbeat`，每輪巡檢更新 timestamp）。

---

## 3. 系統整體架構與核心模組職責 (Architecture & Modules)

專案嚴格遵守單一職責與關注點分離原則，各模組職責如下：

```text
ptt_dc_bot_release/
├── bot.py                  # Discord Client 主程式、/setup 視覺化面板、輪詢迴圈協調器
├── telegram_bot.py         # Telegram Client 主程式 (長輪詢、Slash 指令、行內按鈕選單)
├── email_notifier.py       # Email SMTP 非同步通知供應商 (MIME HTML 卡片、防 Header 注入)
├── delivery.py             # 抽象 NotificationProvider 與多平台 DeliveryDispatcher 派發核心
├── sold_rules.py           # 純函式售出狀態機 (四態判讀、地名/時間誤判過濾)
├── ptt_crawler.py          # aiohttp 爬蟲、P0~P3 優先級閘門、多品項虛擬獨立視角切分 (apply_keyword_view)
├── boards.py & boards.json # 看板單一真理來源 (SSOT) 與別名解析 (消歧義電蝦/PC_Shopping/MobileComm)
├── timeutil.py             # 台北時區 (Asia/Taipei) 處理與 2/29 閏年/跨年回推安全解析
├── groq_scheduler.py       # Groq 三模型動態輪替調度池 (24,000 TPM) 與抗餓死優先級佇列
├── groq_agent.py           # ReAct AI 代理、Prompt Injection 隔離 (wrap_untrusted) 與 Intent Guard
├── storage.py              # SQLite WAL 多租戶管理、Schema v5 動態 Migration、3 代原子熱備份
├── embeds.py               # Discord Embed 卡片排版、防越界截斷與原位更新輔助函式
├── setup_wizard.py         # 零依賴標準庫跨平台互動式安裝設定精靈
├── docs/                   # 模組化部署手冊 (deploy-docker, deploy-synology, deploy-unraid, deploy-linux)
├── PRIVACY.md              # 隱私權政策與 /forget_me 個人資料永久刪除說明
├── tools/eval_sold.py      # 售出判定基準評測工具 (搭配 tests/data/sold_labels.jsonl 80 筆樣本)
└── tests/                  # 100% 離線 Mock 單元與整合測試套件 (250 tests + 20 subtests)
```

### 3.1 核心機制說明

1. **售出狀態機 (`sold_rules.py`)**：
   - 純函式無副作用設計，消除「可洽中壢站」、「結標時間」、「原廠已停售款」等偽陽性。
   - 區分四態：`SOLD`（已售出）、`RESERVED`（暫售/洽中）、`NO_MAIL`（請勿來信）、`AVAILABLE`（正常在架）。
   - 穿透檢查作者簽名檔後最新推文，買家之「排」、「已信」等推文不影響商品售出判定。
2. **多品項獨立虛擬切分 (`ptt_crawler.py::apply_keyword_view`)**：
   - 一篇貼文合售多樣商品時（如：`1. 1060 (售出) / 2. 3060Ti $5800`），依關鍵字切出獨立虛擬品項視角，各自計算價格與售出狀態，不再因一物售出而廢棄全篇貼文。
3. **多租戶與 18 小時原位更新 (`delivery.py`)**：
   - 推播發送後持續追蹤 18 小時；確認售出後直接於 Discord (`edit_message`) 與 Telegram (`editMessageText`) 原訊息加上刪除線，保持對話視窗清爽不重複推播。
   - 修正跨租戶訂閱群組撈取（`get_recent_unmarked_deliveries`），避免先被更新之租戶導致其他租戶漏失更新。
4. **Crawl-Once, Fan-Out 高效快取**：
   - 每個看板每輪僅抓取一次，全域共享內文快取（10 分鐘）與 AI 判定快取（60 分鐘），避免頻繁請求 PTT 觸發 503 限流。
   - 延後標記 `seen_articles`：僅在該看板文章判定與派發全數完成後才寫入已讀，抓取失敗之文章留待下輪重試。
5. **Prompt Injection 縱深防禦 (`groq_agent.py`)**：
   - 所有來自 PTT 之外部非信任內容一律經由 `wrap_untrusted` 進行 XML 標籤轉義並封裝於 `<untrusted_ptt_content>`。
   - 嚴格意圖門禁（Intent Guard）：調用寫入工具（如刪除關鍵字、變更頻道）前，必須比對使用者原始 Prompt 意圖與目標參數，防止惡意貼文誘騙 AI 篡改設定。

---

## 4. 常用維運與測試指令 (Operational & Test Commands)

在進行任何程式碼重構或審查時，請務必在終端機執行以下驗證指令：

### 4.1 執行全套測試套件
```bash
/tmp/ptt_venv/bin/pytest -q
```
*預期產出*：`250 passed, 20 subtests passed in ~1.8s`。

### 4.2 執行售出狀態機基準評測 (80 筆真實樣本)
```bash
/tmp/ptt_venv/bin/python tools/eval_sold.py
```
*預期產出*：
```text
Total records  : 80
Correct        : 80
Accuracy       : 1.0000 (100.00%)
Precision      : 1.0000 (100.00%)
Recall         : 1.0000 (100.00%)
TP: 44, FP: 0, FN: 0, TN: 36
All records classified correctly! (0 mismatches)
```

### 4.3 執行單一模組測試範例
```bash
# 測試售出規則狀態機
/tmp/ptt_venv/bin/pytest tests/test_sold_rules.py -q

# 測試價格與預算解析
/tmp/ptt_venv/bin/pytest tests/test_price_parse.py -q

# 測試多品項虛擬視角
/tmp/ptt_venv/bin/pytest tests/test_keyword_view.py -q

# 測試多平台原位更新與追蹤
/tmp/ptt_venv/bin/pytest tests/test_delivery_followup.py -q

# 測試資安防護與爬蟲優先級
/tmp/ptt_venv/bin/pytest tests/test_phase3_security_crawler.py -q

# 測試心跳健康檢查與文件約束
/tmp/ptt_venv/bin/pytest tests/test_phase4_health_docs.py -q
```

### 4.4 Docker 與服務檢查
```bash
# Docker 容器狀態與心跳健康檢查
docker compose ps
docker inspect --format='{{json .State.Health}}' ptt-dc-bot

# 容器心跳檢查邏輯：同時檢查 /app/data/bot_data.db 與 /app/data/heartbeat 存在且 mtime 在 600 秒內。
```

---

## 5. 開發與審查規範 (Development & Review Conventions)

協同維護審查時，請遵循以下核心規範：

1. **零裝飾性 Emoji 原則**：
   - 程式碼、註解、日誌輸出與非 Discord 使用者介面文字中，嚴格禁止加入裝飾性 Emoji（如 🚀, 🤖, ⚠️, ❌ 等），保持日誌與架構之專業度。
2. **多租戶資料隔離防呆**：
   - 任何涉及 `subscribers`、`keywords`、`fuzzy_rules`、`deliveries` 的資料庫操作，SQL 查詢必須強制以 `subscriber_id` 作為過濾條件，嚴防越權存取。
3. **無破壞性變更約束**：
   - 切勿隨意變更 Discord Slash Command 的指令名稱或參數簽章（避免破壞線上已同步之指令快取）。
   - 切勿刪除或修改 `assets/` 目錄下的 4 張 SVG 圖檔及其在 `README.md` 中的引用路徑。
   - `storage.py` 已升級至 Schema v5，同時在 `settings['schema_version']` 維護向下相容性，切勿破壞 `tests/test_storage_schema_v4.py` 等既有測試。
4. **Git 操作說明**：
   - 本工作目錄為獨立 Git 儲存庫，分支提交已具備合法之 SSH 遠端權限。
   - 推送至 `main` 或推送 `v*` 標籤將自動觸發 GitHub Actions 發行流程。

---

*文件維護：Antigravity Agent & Jason (Project Maintainer)*  
*最後更新時間：2026-11-10*
