# PTT-DC-Bot

[English](README_EN.md) | **繁體中文**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![Groq API](https://img.shields.io/badge/LLM-Groq%20API-f55036)](https://groq.com/)
[![SQLite WAL](https://img.shields.io/badge/Database-SQLite%20WAL-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/wal.html)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

結合規則過濾與 Groq LLM 語意判讀的 PTT 看板監控與 Discord 推播機器人。
主要針對 `HardwareSale`（電蝦二手）、`MacShop`、`nb-shopping`、`Gamesale` 等買賣交易看板設計，解決傳統推播工具常見的「點進去才發現已售出」、「合售文被誤判結案」與「型號數字被當成售價」等問題。

---

## 為什麼做這個專案？（與現有方案的差異）

市面上常見的 PTT 推播工具（如 PttAlertor、RSS 機器人或簡易 Webhook 腳本）大多只做「標題關鍵字比對 + 單向發送訊息」。在二手交易看板實際使用時，通常會遇到幾個痛點，本專案針對這些問題做了以下設計：

1. **已售出自動原位更新（In-place Update）**
   傳統推播發出後就不再追蹤，頻道內常堆滿已經賣掉的無效連結。本工具在推播後會持續追蹤該篇文章 18 小時，除了看標題是否改為售出，也會穿透檢查簽名檔下方**作者本人的最新推文**（如「已售出」、「暫售」、「請勿再來信」），並在確認售出後**直接編輯原本的 Discord 通知卡片加上刪除線**，不額外發訊息洗版。
2. **多品項合售文獨立解析**
   一篇貼文同時賣多樣商品時（例如：`1. GTX 1060 (已售出) / 2. RTX 3060Ti $5800`），一般爬蟲看到內文有「已售出」就會整篇跳過。本工具會針對不同關鍵字切出獨立視角，各自判斷售出狀態與對應價格。
3. **硬體型號與價格消歧義**
   內建型號遮蔽規則，不會把標題或內文的 `RTX 3060`、`12G`、`750W`、`i5-12400` 誤判為商品售價；支援 `5.8k` 等常見簡寫換算與預算上限過濾。
4. **自然語言設定與語意審核（免付費 API 成本）**
   整合 Groq API，支援直接用白話文設定規則（例如：「幫我追 6500 元以內、有原廠保固的 3060Ti」）。底層透過 3 個免費模型建立動態輪替池（合計 24,000 TPM）與 P0~P3 優先級佇列，避免背景巡檢觸發 Rate Limit。
5. **單一實例支援「伺服器共用」與「個人私訊（DM）」**
   底層採用 SQLite WAL 多租戶架構。同一個 Bot 可以放進伺服器頻道做公用推播，群友也能直接私訊 Bot 建立自己的私人追蹤清單，規則與配額彼此完全隔離。

<details>
<summary><b>點擊展開：與現有常見方案的功能對照表（PttAlertor / Webhook / RSS）</b></summary>
<br>

| 評估項目 | 傳統 Webhook 腳本 | PttAlertor | RSS 訂閱機器人 | **PTT-DC-Bot (本專案)** |
| :--- | :--- | :--- | :--- | :--- |
| **互動方式** | 單向推播，需改設定檔 | LINE / TG 指令 | Slash 指令 (僅綁網址) | **原生 Slash 指令 + 私訊自然語言對話 + 翻頁按鈕** |
| **多租戶隔離** | 無 (單一設定) | 依帳號區分 | 依頻道區分 | **支援伺服器頻道公用 + 個人私訊獨立規則** |
| **已售出判定** | 僅看標題文字 | 僅看標題或無 | 無 | **標題正則 + 簽名檔後作者推文 + LLM 語意複審** |
| **過時訊息處理** | 不處理 | 不處理 | 不處理 | **18 小時內自動原位編輯卡片並加上刪除線** |
| **多品項合售文** | 整篇統一判斷 | 整篇統一判斷 | 無 | **依關鍵字切分獨立品項狀態與對應售價** |
| **價格上限過濾** | 簡易正則 (易誤判型號) | 無 | 無 | **排除硬體型號干擾，支援 `k` 單位換算與預算過濾** |
| **歷史現貨搜尋** | 無 | 無 | 無 | **提供 `/scan_unsold` 回溯掃描版上尚未售出的商品** |
| **爬蟲請求最佳化** | 依規則重複抓取 | 集中爬取 | 依賴 RSS 來源更新頻率 | **單次爬取多租戶共享快取 (Crawl-Once, Fan-Out)** |

</details>

---

## 快速開始與部署指南

### 1. 準備前置憑證

在啟動機器人之前，你需要先取得兩組免費憑證：

#### (1) 建立 Discord Bot 並取得 Token
1. 前往 [Discord Developer Portal](https://discord.com/developers/applications) 點擊 **New Application**。
2. 左側選單進入 **Bot**：
   - 點擊 **Reset Token** 取得 `DISCORD_TOKEN`。
   - 往下滑到 **Privileged Gateway Intents**，開啟 **Message Content Intent**（必開，否則無法讀取私訊與一般對話）並儲存。
3. 左側選單進入 **OAuth2 -> URL Generator**：
   - **Scopes** 勾選：`bot` 與 `applications.commands`。
   - **Bot Permissions** 勾選：`Send Messages`、`Embed Links`、`Read Message History`、`View Channels`。
   - 複製最下方產生的網址，在瀏覽器打開並將機器人邀請進你的伺服器。

#### (2) 取得 Groq API Key（免費）
1. 前往 [Groq Console](https://console.groq.com/keys) 註冊並登入。
2. 點擊 **Create API Key**，複製產生的金鑰（格式為 `gsk_...`）。

---

### 2. 設定環境變數

複製專案目錄下的 `.env.example` 為 `.env`：

```bash
cp .env.example .env
chmod 600 .env
```

編輯 `.env`，至少填入剛才取得的兩項必填憑證：

```ini
# 必填：Discord Bot Token 與 Groq API Key
DISCORD_TOKEN=填入你的_discord_bot_token
GROQ_API_KEY=填入你的_groq_api_key
```

> **提示**：`.env` 中的 `DEFAULT_CHANNEL_ID` 與 `DEFAULT_GUILD_ID` 為選填。你可以在機器人啟動後，直接進入 Discord 目標頻道輸入 `/channel_set` 來綁定推播頻道，不一定要手動查 ID 寫進設定檔。

<details>
<summary><b>點擊展開：完整 .env 環境變數參數說明</b></summary>
<br>

| 變數名稱 | 必填 | 預設值 | 說明 |
| :--- | :---: | :--- | :--- |
| `DISCORD_TOKEN` | 是 | 無 | Discord Bot 認證 Token |
| `GROQ_API_KEY` | 是 | 無 | Groq Cloud API 金鑰（用於自然語言助理與售出語意判斷） |
| `DEFAULT_CHANNEL_ID` | 否 | 空 | 預設推播的 Discord 頻道 ID（建議直接進頻道用 `/channel_set` 設定） |
| `DEFAULT_GUILD_ID` | 否 | 空 | 主伺服器 ID（擁有較高配額） |
| `OWNER_USER_ID` | 否 | 空 | 管理員 Discord User ID（留空時自動由主伺服器擁有者識別） |
| `DEFAULT_BOARD` | 否 | `HardwareSale` | 預設監控的 PTT 看板名稱 |
| `POLL_INTERVAL_MIN` | 否 | `90` | 背景巡檢最小間隔秒數 |
| `POLL_INTERVAL_MAX` | 否 | `150` | 背景巡檢最大間隔秒數（隨機抖動避免固定頻率請求） |
| `GROQ_MODEL` | 否 | `openai/gpt-oss-120b` | 主力語意分析模型 |
| `GROQ_FAST_MODEL` | 否 | `openai/gpt-oss-20b` | 輕量快速初篩模型 |
| `DB_PATH` | 否 | `bot_data.db` | SQLite 資料庫儲存路徑（Docker 環境預設為 `/app/data/bot_data.db`） |
| `LOG_PATH` | 否 | `bot.log` | 日誌檔案路徑（5MB 自動滾動保留 3 份） |

</details>

---

### 3. 啟動機器人（三種方式擇一）

#### 方式 A：使用 Docker Compose 部署（推薦）

專案已內建 `Dockerfile` 與 `docker-compose.yml`（預設以非 root 權限運行，並將資料庫與日誌持久化掛載於 `./data`）：

```bash
# 1. 建立資料掛載目錄並啟動容器
mkdir -p data
docker compose up -d --build

# 2. 查看執行日誌（確認出現 Logged in as ... 即代表啟動成功）
docker compose logs -f

# 3. 停止服務
docker compose down
```

#### 方式 B：使用本機 Python 虛擬環境 + `tmux` 常駐

適合直接在 Linux 主機或開發環境運行，內建腳本會在程序異常退出時於 10 秒後自動重啟：

```bash
# 1. 建立虛擬環境並安裝依賴套件 (需要 Python 3.10+)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. 啟動 tmux 背景常駐服務 (Session 名稱: ptt_bot)
bash restart_bg.sh

# 3. 查看即時日誌
tail -f bot.log

# 4. 停止服務
bash stop_bg.sh
```

<details>
<summary><b>方式 C：使用 Linux systemd 系統服務常駐（點擊展開）</b></summary>
<br>

若偏好使用 `systemd` 管理常駐服務，可直接使用專案附帶的 `ptt-bot.service` 範本：

```bash
# 1. 複製服務設定檔並修改其中的 User 與 WorkingDirectory 為實際路徑
sudo cp ptt-bot.service /etc/systemd/system/ptt-bot.service
sudo nano /etc/systemd/system/ptt-bot.service

# 2. 重新載入並設定開機自動啟動
sudo systemctl daemon-reload
sudo systemctl enable --now ptt-bot

# 3. 查看服務狀態
sudo systemctl status ptt-bot
```

</details>

---

## 使用方式說明

機器人啟動後，支援**個人私訊（DM）**與**伺服器頻道**兩種使用情境：
- **個人私訊追蹤**：直接點開機器人的私訊視窗打字或使用指令，推播會直接發到你的私訊（不需管理員權限）。
- **伺服器頻道推播**：管理員先在目標文字頻道執行 `/channel_set` 綁定頻道，之後在頻道內設定的規則就會推播到該頻道。

### 1. 直接用自然語言交辦（私訊直接打字 或 `/ai`）

你可以直接對機器人輸入日常對話，AI 會自動拆解意圖並呼叫對應設定工具：
- `"幫我追蹤 HardwareSale 板販售的 4070S，預算 18000 元以內"`
- `"我想找台北面交、還有原廠保固的 3060Ti，預算 6500"`
- `"幫我掃描目前 HardwareSale 板上還沒賣掉的 6700XT"`
- `"我想買二手 MacBook，幫我找適合的 PTT 看板並加入追蹤"`
- `"列出我目前設定的所有追蹤條件"`

### 2. 設定精確關鍵字（`/keyword_add`）

不消耗每日 AI 對話額度，適合型號明確的快速匹配：
- **空白代表 AND**：`台北 3060TI`（標題需同時包含「台北」與「3060TI」）
- **減號 `-` 代表排除**：`3060 -ti -筆電 -礦`（找純 3060，排除 3060Ti、筆電與礦卡）
- **設定預算上限（`max_price`）**：例如填 `6000`，文章標價高於 6000 元自動略過。

### 3. 設定 AI 模糊語意規則（`/fuzzy_add`）

適合無法單靠標題字面判斷的條件（例如要求保固期限、盒裝完整、特定交易方式）：
- `description`：填入條件描述（如 `RTX 3060Ti 需有盒裝且保固內，非礦卡`）
- `hint_keywords`（建議填寫）：初篩詞（如 `3060,3060ti`），標題命中初篩詞時才會呼叫 LLM 讀內文，加快推播速度。

### 4. 主動掃描版上現貨（`/scan_unsold`）

剛設好規則不想乾等新文章？可執行 `/scan_unsold keyword: 3060TI`，系統會自動往回抓取看板近期文章，過濾掉已售出或已結案的貼文，整理成可翻頁的現貨清單。

<details>
<summary><b>點擊展開：完整 24 組 Slash 指令清單</b></summary>
<br>

#### AI 與搜尋指令
| 指令 | 主要參數 | 說明 |
| :--- | :--- | :--- |
| `/ai` | `prompt`, `scope` | 以自然語言指示 AI 新增規則、找看板或查現貨 |
| `/scan_unsold` | `keyword`, `board`, `max_per_keyword` | 回溯掃描看板近期貼文，過濾出尚未售出的商品清單 |
| `/search` | `keyword`, `board`, `pages` | 呼叫 PTT 原生搜尋查詢歷史文章（不過濾售出狀態） |

#### 精確關鍵字管理
| 指令 | 主要參數 | 說明 |
| :--- | :--- | :--- |
| `/keyword_add` | `keyword`, `board`, `category`, `max_price` | 新增精確關鍵字（支援空白 AND、`-` 排除詞、價格上限） |
| `/keyword_edit` | `keyword_id`, `new_keyword`, `max_price` | 修改現有關鍵字內容或價格上限（填 0 為不限金額） |
| `/keyword_remove` | `keyword`, `scope` | 移除指定的精確關鍵字（支援選單自動補齊） |
| `/keyword_list` | `scope` | 檢視目前啟用的所有關鍵字、模糊規則與推播狀態 |
| `/keyword_clear` | `scope` | 清空當前範圍內的所有精確關鍵字 |

#### AI 模糊規則與看板管理
| 指令 | 主要參數 | 說明 |
| :--- | :--- | :--- |
| `/fuzzy_add` | `description`, `board`, `category`, `hint_keywords` | 新增自然語言模糊追蹤規則 |
| `/fuzzy_remove` | `rule`, `scope` | 移除指定的模糊規則 |
| `/fuzzy_list` | `scope` | 檢視目前啟用的模糊規則清單 |
| `/board_add` | `board`, `scope` | 驗證並新增要監控的 PTT 看板（首次加入只建立基準線不洗版） |
| `/board_remove` | `board`, `scope` | 移除指定看板與其關聯規則 |
| `/board_list` | 無 | 查看系統目前正在輪詢的所有 PTT 看板 |

#### 頻道設定、個人狀態與管理
| 指令 | 主要參數 | 說明 |
| :--- | :--- | :--- |
| `/channel_set` | `channel`, `scope` | 設定推播通知的目標頻道（私訊中執行則設為私訊通知） |
| `/pause` / `/resume` | `scope` | 暫停或恢復推播通知（保留既有規則設定） |
| `/me` | 無 | 查看個人目前的追蹤規則、私訊連線狀態與今日剩餘配額 |
| `/forget_me` | 無 | 徹底刪除個人在資料庫中的所有規則、推播紀錄與對話記憶 |
| `/status` | `scope` | 檢視系統運行狀態、Groq 佇列負載與爬蟲狀態 |
| `/check_now` | 無 | 立即手動觸發一次看板巡檢（非管理員有 5 分鐘冷卻） |
| `/push_mode` | `mode` (`keyword` / `all`) | 切換關鍵字匹配或全看板新文推播（限管理員） |
| `/admin` | `action`, `target_id`, `tier` | 系統擁有者專用：檢視全局統計、調整用戶配額階層或黑名單 |
| `/help` | 無 | 顯示指令摘要與操作說明 |

</details>

---

## 進階架構與技術細節

以下整理本專案的內部架構、防限流機制與安全防護設計，可依需求點擊展開檢視：

<details>
<summary><b>1. 系統架構與目錄結構</b></summary>
<br>

```text
ptt-dc-bot/
├── bot.py               # Discord Client 主程式、24 組 Slash 指令與背景輪詢迴圈
├── config.py            # 環境變數載入、租戶配額常數與輸入消毒規則
├── storage.py           # SQLite WAL 多租戶資料庫、自動 Schema 遷移與原子備份
├── ptt_crawler.py       # 非同步 PTT 爬蟲、雙層售出判定与多品項獨立視角解析
├── groq_scheduler.py    # Groq API 優先級佇列（P0~P3）與三模型動態限流調度器
├── groq_agent.py        # ReAct AI 代理、工具呼叫、權限門禁與 Prompt Injection 防護
├── delivery.py          # 跨頻道/私訊推播分發器、3 天去重雜湊與 18h 售出原位更新
├── embeds.py            # Discord Embed 卡片建構、長度邊界截斷與防搶翻頁按鈕
├── Dockerfile           # Python 3.11-slim 容器映像檔定義（非 root 帳號運行）
├── docker-compose.yml   # 容器編排、資源配額與 ./data 持久化掛載設定
├── ptt-bot.service      # Linux systemd 常駐服務設定範本
├── start_bg.sh          # tmux 背景守護啟動腳本（異常退出 10 秒自動重啟）
├── stop_bg.sh           # tmux 服務停止腳本
├── restart_bg.sh        # tmux 服務重啟腳本
└── tests/               # 離線單元測試與安全性回歸測試套件
```

**背景巡檢流程（Crawl-Once, Fan-Out）**：
1. **聚合活躍看板**：僅抓取有「啟用中訂閱者規則」的看板，無規則看板自動跳過。
2. **單次爬取共享**：每個看板每輪只發送一次 HTTP 請求，內文快取（10 分鐘）與 AI 判定結果（60 分鐘）全租戶共享，避免重複抓取觸發 PTT 503 限制。
3. **多品項視角切分**：透過 `apply_keyword_view` 為不同關鍵字建立虛擬視角，分離個別品項的售價與售出狀態。
4. **18h 原位追蹤**：背景以 Fair Round-Robin 輪詢檢查過去 18 小時內推播過的文章，發現售出即原位編輯 Discord 訊息。

</details>

<details>
<summary><b>2. Groq 多模型調度器與配額階層設計</b></summary>
<br>

Groq 免費方案對單一模型有每分鐘 Token 數（TPM）與每日請求數（RPD）限制。為了避免背景爬蟲與使用者對話互相卡死，`groq_scheduler.py` 實作了以下機制：

- **三模型動態負載池**：輪替調度 `openai/gpt-oss-120b`、`qwen/qwen3.8-27b` 與 `openai/gpt-oss-20b` 三個獨立模型桶，將免費可用額度擴充至 3 倍（合計約 24,000 TPM）。
- **四級優先佇列**：
  - `P0 (Interactive)`：使用者私訊與 `/ai` 對話（最優先處理）
  - `P1 (Realtime)`：背景新文章推播的語意審核
  - `P2 (Batch Scan)`：`/scan_unsold` 批次歷史掃描（當模型剩餘配額低於 30% 時自動讓位給 P0/P1）
  - `P3 (Maintenance)`：低優先級背景工作

**預設租戶配額表**（背景自動推播不消耗個人的每日對話或掃描次數）：

| 配額項目 | 個人私訊 (`friend`) | 一般伺服器 (`free`) | 管理員 / 主伺服器 (`owner`) |
| :--- | :---: | :---: | :---: |
| 精確關鍵字上限 | 30 組 | 10 組 | 999 組 |
| AI 模糊規則上限 | 8 組 | 3 組 | 999 組 |
| 同時追蹤看板數 | 5 個 | 5 個 | 20 個 |
| 每日 `/ai` 與私訊對話 | 60 次 / 日 | 20 次 / 日 | 無限制 |
| 每日 `/scan_unsold` 掃描 | 25 次 / 日 | 8 次 / 日 | 無限制 |

</details>

<details>
<summary><b>3. 安全性防護設計（Prompt Injection / RBAC / SSRF / 容器加固）</b></summary>
<br>

針對公開部署常見的攻擊向量，專案實作了以下防護：
- **Prompt Injection 隔離**：所有爬取自 PTT 的外部文章皆經由 `escape_prompt_xml` 轉義標籤後包裹於 `<untrusted_ptt_content>` 內；且工具執行層設有**意圖鎖定（Intent Guard）**，若使用者原始指令沒有新增/刪除意圖，外部文章內容無法誘使 AI 呼叫修改或刪除規則的工具。
- **多租戶 RBAC 權限隔離**：一般伺服器成員無權修改伺服器共用規則；資料庫刪除與更新操作強制綁定 `subscriber_id`，防止跨租戶越權存取。
- **SSRF 與查詢安全**：爬蟲嚴格驗證目標網域為 `https://www.ptt.cc/bbs/`，並強制關閉 HTTP 自動重定向（`allow_redirects=False`）；SQLite 全面採用參數化查詢與 `LIKE ... ESCAPE '/'`。
- **錯誤訊息脫敏與最小權限**：前端異常回覆全面遮蔽內部路徑與 Traceback；Docker 容器以 UID 1000 (`botuser`) 運行、程式碼目錄唯讀，並移除所有 Linux Capabilities (`cap_drop: [ALL]`)。

</details>

<details>
<summary><b>4. 常見問題與排錯（FAQ）</b></summary>
<br>

**Q1：在私訊設定了規則，但一直沒收到私訊推播？**
請先輸入 `/me` 檢查狀態。如果顯示「曾發生私訊失敗」，代表你的 Discord 隱私設定阻擋了機器人訊息。請至 Discord「使用者設定 -> 隱私與安全」開啟共同伺服器的「允許來自伺服器成員的私人訊息」，接著隨便傳一則訊息給機器人，就會自動恢復推播。

**Q2：為什麼在伺服器頻道輸入指令會顯示「權限不足」？**
在伺服器頻道直接下指令預設是修改「整個伺服器的共用規則」，需要伺服器管理員權限。如果你只是想設定自己的追蹤清單，請**直接私訊機器人**，或是在使用指令時將 `scope` 參數選為 `個人專屬追蹤 (user)`。

**Q3：為什麼某篇文章符合關鍵字卻沒有推播？**
系統會自動排除以下文章：(1) 發文超過 21 天的舊文；(2) 標題或作者推文已標註售出/暫售/洽中；(3) 同一賣家 3 天內重複重發的相同商品；(4) 內文標價高於你設定的 `max_price`。若想查詢包含已售出的所有文章，請改用 `/search`。

</details>

---

## 執行測試

專案包含離線單元測試與安全性回歸測試（使用本地臨時 SQLite 資料庫與 Mock 物件，不需連網或填入真實 API Key 即可執行）：

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```
