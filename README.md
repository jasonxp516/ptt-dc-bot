# PTT-DC-Bot

[English](README_EN.md) | **繁體中文**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![Groq API](https://img.shields.io/badge/LLM-Groq%20API-f55036)](https://groq.com/)
[![Docker Image](https://img.shields.io/badge/Docker-GHCR%20Multi--Arch-2496ED?logo=docker&logoColor=white)](https://github.com/jasonxp516/ptt-dc-bot/pkgs/container/ptt-dc-bot)
[![SQLite WAL](https://img.shields.io/badge/Database-SQLite%20WAL-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/wal.html)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

結合規則過濾與 Groq LLM 語意判讀的 PTT 看板監控與多平台推播機器人（支援 Discord、Telegram 與 Email SMTP）。
主要針對 `HardwareSale`（電蝦二手）、`MacShop`（蘋果二手）、`nb-shopping`（筆電交易）、`Gamesale`（遊戲交易）、`MobileComm`（手機通訊）等買賣看板設計，解決傳統推播工具常見的「點進去才發現已售出」、「合售文被誤判結案」與「型號數字被當成售價」等問題。

---

## 為什麼做這個專案？（核心功能特色）

市面上常見的 PTT 推播工具大多僅做「標題關鍵字比對 + 單向發送訊息」。在二手交易看板實際使用時，往往面臨資訊過期與誤報困擾。本專案針對這些痛點設計了專門機制：

1. **已售出自動原位更新（In-place Update）**  
   推播後持續追蹤該貼文 18 小時，穿透檢查簽名檔下方**作者本人的最新推文**（如「已售出」、「暫售」、「洽中」）。確認售出後**直接編輯原本的 Discord 通知卡片加上刪除線**，不重複洗版。
2. **多品項合售文獨立解析**  
   一篇貼文同時販售多樣商品時（例如：`1. GTX 1060 (已售出) / 2. RTX 3060Ti $5800`），一般爬蟲偵測到內文含「已售出」就會略過整篇。本工具會為不同關鍵字切出獨立視角，各自判斷售出狀態與對應價格。
3. **硬體型號與價格消歧義**  
   內建型號遮蔽規則，不會將標題或內文的 `RTX 3060`、`12G`、`750W`、`i5-12400` 誤判為售價；支援 `5.8k` 等常見簡寫換算與預算上限過濾。
4. **自然語言設定與語意審核（免付費 API 成本）**  
   整合免費 Groq API，支援直接以白話文交辦規則（例如：「幫我追 6500 元以內、有原廠保固的 3060Ti」）。底層透過 3 個免費模型建立動態輪替池（合計 24,000 TPM）與 P0~P3 優先級佇列，確保背景巡檢不觸發 Rate Limit。
5. **單一實例支援「伺服器公用」與「個人私訊（DM）」**  
   採用 SQLite WAL 多租戶架構。同一個 Bot 可在伺服器公用頻道推播，成員也能直接私訊 Bot 建立私人追蹤清單，規則與配額彼此完全隔離。

<details>
<summary><b>點擊展開：與現有常見方案的功能對照表（PttAlertor / Webhook / RSS）</b></summary>
<br>

| 評估項目 | 傳統 Webhook 腳本 | PttAlertor | RSS 訂閱機器人 | **PTT-DC-Bot (本專案)** |
| :--- | :--- | :--- | :--- | :--- |
| **互動方式** | 單向推播，需手動改設定檔 | LINE / TG 指令 | Slash 指令 (僅綁網址) | **原生 `/setup` 視覺化 UI + 自然語言對話 + 翻頁按鈕** |
| **多租戶隔離** | 無 (單一設定) | 依帳號區分 | 依頻道區分 | **支援伺服器頻道公用 + 個人私訊獨立規則** |
| **已售出判定** | 僅檢查標題文字 | 僅檢查標題或無 | 無 | **標題正則 + 簽名檔後作者推文 + LLM 語意複審** |
| **過時訊息處理** | 不處理 | 不處理 | 不處理 | **18 小時內自動原位編輯卡片並加上刪除線** |
| **多品項合售文** | 整篇統一判斷 | 整篇統一判斷 | 無 | **依關鍵字切分獨立品項狀態與對應售價** |
| **價格上限過濾** | 簡易正則 (易誤判型號) | 無 | 無 | **排除硬體型號干擾，支援 `k` 單位換算與預算過濾** |
| **歷史現貨搜尋** | 無 | 無 | 無 | **提供 `/scan_unsold` 回溯掃描版上尚未售出的商品** |
| **爬蟲請求最佳化** | 依規則重複抓取 | 集中爬取 | 依賴 RSS 來源頻率 | **單次爬取多租戶共享快取 (Crawl-Once, Fan-Out)** |

</details>

---

## 快速開始：三大部署方式（三選一）

在開始之前，請先根據欲使用的推播管道準備對應憑證（至少具備一種推播管道憑證）：
1. **Discord Bot Token（若使用 Discord）**：至 [Discord Developer Portal](https://discord.com/developers/applications) 建立 Application，在「Bot」分頁點擊 **Reset Token** 取得金鑰；**請務必在下方開啟 `Message Content Intent`**。
2. **Telegram Bot Token 與 Chat ID（若使用 Telegram）**：至 Telegram [@BotFather](https://t.me/BotFather) 取得 Token，並取得個人或群組 Chat ID。
3. **Email SMTP 應用程式密碼（若使用 Email）**：至 [Google 帳戶安全性](https://myaccount.google.com/security) 產生 16 碼「應用程式密碼」。
4. **Groq API Key（推薦，選用）**：至 [Groq Console](https://console.groq.com/keys) 免費註冊並點擊 **Create API Key** 取得金鑰（格式為 `gsk_...`），用於啟用自然語言白話文對話與 AI 售出語意審核。

---

### 方式一：一行指令自動安裝與引導（推薦）

打開終端機（Terminal 或 PowerShell），貼上對應系統的一行指令即可自動完成「下載專案 -> 建立虛擬環境 -> 安裝相依套件 -> 啟動互動設定精靈 -> 產生邀請網址 -> 啟動服務」：

#### Linux / macOS
```bash
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.sh | bash
```

#### Windows (PowerShell)
```powershell
irm https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.ps1 | iex
```

*特點說明*：
- 自動檢測 Python 3.10+ 環境（若系統缺少 Python，Windows 腳本會提示透過 winget 自動安裝）。
- 自動建立獨立虛擬環境（`.venv`），不污染全域環境。
- 自動進入終端機設定精靈，依畫面提示貼上 Token 與 Key 即可。
- 安裝完成後提供立即啟動選項（Linux 支援選擇以 tmux 背景常駐運行）。

---

### 方式二：GitHub Releases 雙擊即開免安裝包

不習慣使用終端機指令的使用者，可直接下載官方打包發行的免安裝壓縮包：

1. 前往專案的 [GitHub Releases 發行頁面](https://github.com/jasonxp516/ptt-dc-bot/releases) 下載最新版本檔案：
   - **Windows**：下載 `ptt-dc-bot-<版本>-windows.zip`
   - **macOS**：下載 `ptt-dc-bot-<版本>-macos.tar.gz`
   - **Linux**：下載 `ptt-dc-bot-<版本>-linux.tar.gz`（例如 `ptt-dc-bot-v1.0.0-beta.1-linux.tar.gz`）
2. 將壓縮檔解壓縮至任意目錄（例如桌面或個人資料夾，Linux 可使用 `tar -xzf ptt-dc-bot-*-linux.tar.gz` 解壓縮）。
3. 執行啟動器：
   - **Windows**：滑鼠雙擊 **`start_windows.bat`**
   - **macOS**：滑鼠雙擊 **`start_mac.command`**
   - **Linux**：終端機進入解壓縮目錄執行 **`./install.sh`** 進行初始化設定（或執行 `./start_bg.sh` 常駐啟動 / `python3 bot.py` 前台執行）
4. 首次執行時會自動初始化環境並彈出設定精靈，依指示貼上金鑰後即自動連線啟動。

*守護機制*：
- Windows 啟動器內建自動守護迴圈：若遇到網路瞬斷或程式異常退出，會在 10 秒後自動嘗試重新連線；關閉視窗或在設定階段終止則會乾淨停止。

---

### 方式三：Docker Compose 與 GHCR 預建映像檔

適合 Linux 伺服器、樹莓派、家用主機或 NAS（Synology / unRAID / QNAP）容器化部署。官方提供多架構（AMD64 / ARM64）自動建置映像檔：`ghcr.io/jasonxp516/ptt-dc-bot:latest`。

#### 1. 標準 Docker Compose 部署

建立工作目錄並準備設定檔：

```bash
mkdir -p ptt-dc-bot/data && cd ptt-dc-bot

# 下載官方 docker-compose.yml 與設定範本
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/docker-compose.yml -o docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/.env.example -o .env

# 設定安全權限並編輯金鑰
chmod 600 .env
nano .env
```

在 `.env` 中填入憑證：
```ini
DISCORD_TOKEN=填入你的_discord_bot_token
GROQ_API_KEY=填入你的_groq_api_key
```

啟動容器：
```bash
# 取得最新預建映像檔並於背景啟動
docker compose pull && docker compose up -d

# 檢視即時運行日誌（出現 Logged in as ... 即代表成功）
docker compose logs -f

# 停止服務
docker compose down
```

`docker-compose.yml` 預設配置已啟用最小權限（`cap_drop: ALL`、`no-new-privileges: true`）、資源限制（1.0 CPU、512MB RAM）以及資料庫持久化掛載（`./data:/app/data`）。

---

#### 2. 群暉 Synology NAS (Container Manager) 部署步驟

1. **準備目錄與設定檔**：
   - 開啟 DSM 的 **File Station**，在 `docker` 共用資料夾下建立目錄結構：
     ```text
     /docker/ptt-dc-bot/
     ├── .env           (文字檔，填入 DISCORD_TOKEN 與 GROQ_API_KEY)
     └── data/          (空目錄，供 SQLite 資料庫與日誌持久化儲存)
     ```
2. **建立專案 (Container Manager)**：
   - 開啟 **Container Manager** 應用程式，切換至「**專案 (Project)**」分頁。
   - 點擊「**新增 (Create)**」：
     - **專案名稱**：`ptt-dc-bot`
     - **路徑**：選取剛才建立的 `/docker/ptt-dc-bot`
     - **來源**：選擇「建立 docker-compose.yaml」，並貼入專案的 `docker-compose.yml` 內容。
   - 點擊下一步，Container Manager 將自動下載 `ghcr.io/jasonxp516/ptt-dc-bot:latest` 映像檔並啟動容器。
3. **確認運行**：
   - 點選該容器的「詳細資訊」->「日誌」，確認輸出 `Logged in as ...` 即可。

---

#### 3. unRAID 容器化部署步驟

1. 進入 unRAID Web 管理介面，切換至 **Docker** 分頁，滑到最下方點擊 **Add Container**。
2. 填寫容器參數：
   - **Name**：`ptt-dc-bot`
   - **Repository**：`ghcr.io/jasonxp516/ptt-dc-bot:latest`
   - **Restart Policy**：`unless-stopped`
3. 點擊 **Add another Path, Port, Variable, or Device** 加入以下配置：
   - **目錄掛載 (Path)**：
     - Container Path: `/app/data`
     - Host Path: `/mnt/user/appdata/ptt-dc-bot`
     - Access Mode: `Read/Write`
   - **環境變數 (Variable)**：
     - Name: `DISCORD_TOKEN`, Value: `你的 Discord Token`
     - Name: `GROQ_API_KEY`, Value: `你的 Groq API Key`
     - Name: `TZ`, Value: `Asia/Taipei`
4. 點擊 **Apply**，容器建置完成後即自動於背景常駐運行。

---

## 多平台支援與可行性綜合評估

本專案支援多平台推播架構，使用者可依自身需求選用單一或多個通知管道（例如：以 Telegram 作為個人即時手機推播、Discord 作為伺服器社群廣播、Email 作為重要信件存檔）：

### 四大通知平台特性綜合比較表

| 評估維度 | Discord (現行正式版) | Telegram Bot (Beta 新增) | Email SMTP (Beta 新增) | LINE Messaging API (受限不支援) |
| :--- | :--- | :--- | :--- | :--- |
| **運作成本** | [支援] 完全免費 | [支援] 完全免費 | [支援] 完全免費 (Gmail / 個人伺服器) | [不支援] 每月僅 200 則免費，超額月費 NT$800 起 |
| **連線機制** | WebSocket Gateway (出站) | Long Polling 長輪詢 (出站) | SMTP TCP (出站) | [限制] 雙向強制需公網 HTTPS Webhook |
| **NAT/家用穿透** | [支援] 無需公網 IP / 免憑證 | [支援] 無需公網 IP / 免憑證 | [支援] 無需公網 IP / 免憑證 | [限制] 需固定 IP、網域、SSL 或反向穿透工具 |
| **推播數量限制** | 無硬性上限 (遵循 API 限速) | 無硬性上限 (全域 30 msg/s) | 每日約 500 封 (Gmail 限制) | [限制] 每月僅 200 則主動推播硬性上限 |
| **雙向互動能力** | 原生 Slash 指令、按鈕、下拉選單 | 文字斜線指令、行內按鈕選單 | 僅單向通知 (雙向需另建 IMAP 輪詢) | 需自行架設 Webhook 伺服器 |
| **售出原位更新** | [支援] 支援 (原訊息編輯劃線) | [支援] 支援 (`editMessageText` 劃線) | [限制] 不支援 (信件送達後不可竄改) | [限制] 僅能另發推播更新 (加速耗盡額度) |
| **外部相依套件** | `discord.py` | [支援] 零新增套件 (沿用 `aiohttp`) | [支援] 零新增套件 (內建 `smtplib`) | 需新增 HTTP Webhook 伺服器套件 |
| **主要適用場景** | 伺服器社群廣播、個人私訊盯梢 | 個人即時通知、跨平台群組推播 | 關鍵重要商品備份、低頻郵件提醒 | 現階段不建議使用 |
| **可行性結論** | **正式版已支援** | **推薦使用 (Beta 支援)** | **推薦使用 (Beta 支援)** | **受限不支援 (成本與技術障礙)** |

*關鍵結論*：
- **Discord & Telegram**：推薦用於即時雙向互動、豐富按鈕介面與 18 小時商品售出自動原位劃線更新。
- **Email (SMTP)**：推薦用於重要通知永久留存歸檔，跨電腦與行動裝置零穿透限制。
- **LINE**：因 LINE Notify 於 2025/3/31 終止服務，且 LINE Messaging API 每月僅 200 則免費主動推播上限，自架成本過高，現階段不予支援。

---

## 多平台設定與初次啟動教學（點擊展開）

請點擊下方摺疊區塊展開對應平台的完整設定指引：

<details>
<summary><b>[教學] Discord 機器人加入與頻道設定</b></summary>
<br>

#### 步驟 1：建立 Discord Application 與取得 Token
1. 開啟 [Discord Developer Portal](https://discord.com/developers/applications) 並登入您的 Discord 帳號。
2. 點擊右上角 **New Application**，輸入應用程式名稱（例如 `PTT-Tracker`）。
3. 切換至左側選單的 **Bot** 分頁，點擊 **Reset Token** 並複製生成的 Bot Token。
4. **重要設定**：在同一頁面向下捲動至 **Privileged Gateway Intents** 區塊，務必將 **Message Content Intent** 切換為開啟（ON）並點擊儲存變更。若未開啟此選項，機器人將無法讀取指令與私訊。

#### 步驟 2：產生邀請連結並將機器人加入伺服器（OAuth2 權限說明）
1. 切換至左側選單的 **OAuth2** -> **URL Generator**。
2. 在 **Scopes** 勾選：
   - `bot`
   - `applications.commands`
3. 在下方出現的 **Bot Permissions** 勾選以下必要權限（權限整數為 `274877975552`）：
   - `View Channels`（檢視頻道）
   - `Send Messages`（發送訊息）
   - `Embed Links`（嵌入連結，卡片排版必備）
   - `Read Message History`（讀取歷史訊息）
4. 複製底部產生的專屬邀請網址，貼至瀏覽器開啟並選擇欲加入的 Discord 伺服器完成授權。
   - 範例網址格式：`https://discord.com/oauth2/authorize?client_id=<你的_CLIENT_ID>&permissions=274877975552&scope=bot%20applications.commands`

#### 步驟 3：首次連線自動私訊引導 (Clean Boot Welcome DM)
當機器人首次登入且系統處於全新安裝狀態（尚未綁定任何推播頻道且無關鍵字）時：
1. 終端機會印出顯著的提示訊息：
   ```text
   ========================================================================
   [PTT-DC-Bot] 首次啟動導引：
   機器人已成功登入！請在 Discord 伺服器或私訊中輸入 /setup 快速完成推播與看板設定。
   ========================================================================
   ```
2. 機器人會主動向 Application Owner（機器人建立者）發送一則歡迎私訊，提供快速開始 4 步驟教學與核心指令摘要。

#### 步驟 4：`/setup` 視覺化互動面板操作流程
在目標伺服器文字頻道中輸入 `/setup`（或直接在機器人的私訊視窗中輸入 `/setup`），即可喚出視覺化控制面板：

<p align="center">
  <img src="assets/setup_control_panel.svg" alt="PTT-DC-Bot /setup 視覺化互動設定面板" width="680"/>
</p>

面板四大操作步驟：
1. **第一步：點擊「綁定此頻道為推播頻道」按鈕**  
   - 一鍵將目前所在的文字頻道設為推播目標。
   - 若在與機器人的私訊（DM）視窗點擊，則會直接設定為個人專屬私訊推播。
2. **第二步：在「快速選擇監控看板」下拉選單勾選看板**  
   - 下拉選單已預載社群最熱門的二手交易看板：
     - `HardwareSale`：電蝦二手（桌機零組件、顯示卡、周邊設備）
     - `MacShop`：蘋果二手（MacBook、iPhone、iPad、原廠配件）
     - `nb-shopping`：筆電交易（各廠牌二手筆記型電腦）
     - `Gamesale`：遊戲交易（家用主機、Switch / PS5 遊戲片）
     - `MobileComm`：手機通訊（各大品牌智慧型手機、通訊周邊）
   - 支援單選或多選，勾選後系統立即建立文章基準線，避免舊文洗版。

<p align="center">
  <img src="assets/dropdown_boards_select.svg" alt="快速選擇監控看板下拉選單" width="680"/>
</p>

3. **第三步：點擊「新增追蹤關鍵字」按鈕（彈出 Modal 表單）**  
   - 點擊後彈出表單視窗，填寫以下欄位：
     - **追蹤關鍵字（必填）**：支援多詞空格（AND 邏輯，如 `3060Ti 台北`）與減號排除（如 `3060 -筆電 -礦`）。
     - **目標看板（選填）**：預設為 `HardwareSale`，可自行指定其他看板。
     - **限定分類（選填）**：預設為 `賣`（僅過濾販售文章，排除徵求文）。
     - **預算上限（選填）**：填入純數字（如 `6500`），文章標價高於此金額時自動過濾不推播。

<p align="center">
  <img src="assets/keyword_modal_form.svg" alt="新增追蹤關鍵字彈出表單" width="560"/>
</p>

4. **第四步：點擊「測試推播通知」按鈕**  
   - 機器人會立即向已綁定的推播目標發送一張格式完整的測試通知 Embed 卡片。
   - 藉此確認 Bot 是否具備該頻道的發言權限，以及卡片排版是否正常送達。

<p align="center">
  <img src="assets/notification_embed_preview.svg" alt="即時推播通知 Embed 卡片預覽" width="680"/>
</p>

</details>

<details>
<summary><b>[教學] Telegram 機器人建立與推播綁定</b></summary>
<br>

#### 步驟 1：透過 @BotFather 建立 Telegram 機器人
1. 開啟 Telegram，在搜尋列搜尋官方認證帳號 [@BotFather](https://t.me/BotFather) 並開啟對話。
2. 發送指令 `/newbot`。
3. 依提示輸入機器人的顯示名稱（例如 `PTT 追蹤機器人`）。
4. 輸入機器人的使用者名稱（Username，結尾必須為 `bot` 或 `_bot`，例如 `my_ptt_tracker_bot`）。
5. 建立完成後，@BotFather 會回傳 HTTP API Token，格式為 `<bot_id>:<secret>`（例如 `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`）。請妥善保存此 Token。

#### 步驟 2：啟動機器人並取得專屬 Chat ID
1. 在 Telegram 搜尋剛剛建立的機器人 Username 並進入對話視窗。
2. 點擊底部的 **Start** 按鈕，或手動發送 `/start` 指令。
3. 機器人會立即回應歡迎卡片，卡片內即清楚標明您的專屬 `Chat ID`（純數字例如 `123456789`）與訂閱者代號。
   - 若欲在 Telegram 群組接收推播，請將機器人拉入群組，並在群組內發送 `/start`，取得以 `-` 或 `-100` 開頭的群組 Chat ID。

#### 步驟 3：配置環境變數或執行設定精靈
- **方式 A（使用設定精靈，推薦）**：執行 `./install.sh` 或 `python3 setup_wizard.py`，平台選單選擇 `2`（Telegram）或 `1,2`（Discord + Telegram），貼入 Bot Token 與 Chat ID，精靈會自動調用 `getMe` 與 `getChat` 完成連線驗證。
- **方式 B（直接編輯 .env）**：
  ```ini
  ENABLE_TELEGRAM=true
  TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
  TELEGRAM_CHAT_ID=123456789
  ```

#### 步驟 4：日常指令與互動管理
- `/start`：顯示歡迎資訊、Chat ID 與指令摘要。
- `/help`：顯示完整指令參數說明與使用範例。
- `/keyword_add <看板> <分類> <關鍵字> [預算]`：新增追蹤條件（例如 `/keyword_add HardwareSale 賣 3060Ti 7000`；亦支援簡化語法 `/keyword_add 4070Super 18000`）。
- `/keyword_list`：檢視目前啟用的所有追蹤關鍵字。清單下方會自動生成對應的行內按鈕（Inline Keyboard），點擊即可一鍵刪除指定規則。
- `/pause` 與 `/resume`：隨時切換推播通知之暫停與恢復，不影響已設定之關鍵字清單。
- `/status`：檢視各看板爬蟲連線、更新基準線狀態與歷史累積推播量。
- `/me`：查看個人訂閱等級、每日額度使用狀況與規則數量。

#### 步驟 5：特色功能說明
- **PTT 原文一鍵閱讀**：每則推播卡片下方均附帶「前往 PTT 原文」行內按鈕，點擊可直接在瀏覽器開啟文章。
- **商品售出自動劃線（In-Place Edit）**：當文章標題更新或作者於推文標註「已售出」時，機器人會透過 `editMessageText` API 自動在原訊息加上刪除線與 `[已售出]` 標記，保持對話視窗整潔不洗版。
- **長輪詢無痛穿透**：基於出站長輪詢（Long Polling）技術，無需公網 IP、網域名稱或 SSL 憑證，家用網路與防火牆後端皆可隨開即用。

</details>

<details>
<summary><b>[教學] Email (SMTP) 郵件通知與 App Password 設定</b></summary>
<br>

#### 步驟 1：啟用 Gmail 兩步驟驗證並產生應用程式密碼 (App Password)
若使用 Gmail 作為發信伺服器，Google 政策規定不可直接使用個人登入密碼，必須使用專用之「應用程式密碼」：
1. 登入 Google 帳戶並前往 [安全性設定中心](https://myaccount.google.com/security)。
2. 確認「如何登入 Google」區塊中的 **兩步驟驗證 (2-Step Verification)** 狀態為已啟用。
3. 在搜尋欄搜尋「應用程式密碼」或直接前往 [應用程式密碼設定頁面](https://myaccount.google.com/apppasswords)。
4. 輸入自訂名稱（例如 `PTT-Bot`），點擊「建立」。
5. 系統會顯示一組 **16 碼英文字母專用密碼**（例如 `abcd efgh ijkl mnop`）。請複製並妥善保存（密碼中間的空格可保留亦可去除）。

#### 步驟 2：配置環境變數或執行設定精靈
- **方式 A（使用設定精靈，推薦）**：執行 `./install.sh` 或 `python3 setup_wizard.py`，平台選單選擇 `3`（Email），依序輸入 SMTP 主機、連接埠、使用者帳號、16 碼應用程式密碼與收件者信箱，精靈會自動執行即時 SMTP 連線與認證驗證。
- **方式 B（直接編輯 .env）**：
  ```ini
  ENABLE_EMAIL=true
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=your_name@gmail.com
  SMTP_PASSWORD=abcdefghijklmnop
  SMTP_USE_TLS=true
  EMAIL_FROM=your_name@gmail.com
  EMAIL_FROM_NAME=PTT 智慧追蹤機器人
  EMAIL_TO=recipient1@gmail.com, recipient2@example.com
  ```

#### 步驟 3：多收件人支援與排版特色
- **多收件人分發**：`EMAIL_TO` 支援使用逗號或分號分隔多組信箱，系統會自動解析 RFC 5322 格式並進行重複項過濾。
- **響應式 HTML 卡片**：採用標準 MIME `multipart/alternative` 架構，在支援 HTML 之郵件客戶端呈現精美卡片（包含看板標籤、商品標題超連結、作者、價格高亮標籤與重點摘錄）；在純文字環境則自動降級顯示清晰文字。
- **安全防護架構**：
  - **防禦標頭注入 (Anti-Header Injection)**：自動清理信件主旨與收寄件人標頭中的換行符號（`\r`, `\n`），防止 SMTP 指令遭惡意竄改。
  - **防禦 XSS 攻擊**：PTT 文章標題、內文與作者資訊皆經嚴密之 `html.escape` 轉義處理，確保在各家 Webmail 與行動裝置客戶端安全顯示。
- **非同步非阻塞運作**：透過標準函式庫 `smtplib` 與 `asyncio.to_thread` 調度至背景執行緒集區發信，完全不佔用主非同步事件迴圈，亦無需引入額外第三方套件。
- **注意事項**：因電子郵件協定特性，信件一旦發送至收件匣即無法進行遠端原位修改（不支援售出自動劃線功能）。

</details>

<details>
<summary><b>[說明] LINE 平台可行性與限制深度分析</b></summary>
<br>

許多使用者詢問為何本專案現階段未優先實作 LINE 平台推播，以下說明關鍵技術與成本限制：

#### 1. LINE Notify 服務正式終止 (2025/03/31)
- LINE 官方已於 **2025 年 3 月 31 日** 全面終止 LINE Notify 服務。
- 所有歷史 Access Token 及 `https://notify-api.line.me/api/notify` API 端點已全面失效（HTTP 410 Gone）。過往免費、免伺服器、無限推播之簡易整合管道已不復存在。

#### 2. LINE Messaging API 免費額度與高額成本障礙
- **每月僅 200 則免費主動推播**：LINE 官方帳號免費方案每月僅提供 200 則主動發送額度（`pushMessage` / `multicast`）。
- **高頻監控迅速耗盡**：PTT 熱門看板（如 HardwareSale、MacShop、Lifeismoney）單日命中文章數常達 10~20 篇。單人使用下，整月額度通常在第 10~15 天即告用罄；若有 3 位以上訂閱者，數日內即全面中斷。
- **超額直接封鎖與付費門檻**：免費方案一旦達 200 則上限，後續所有推播直接中斷（拋出 HTTP 429 錯誤），直到次月 1 號重置。若欲升級付費方案，中用量方案約 NT$800/月（5,000 則），高用量方案約 NT$1,200/月（30,000 則）。對於開源自架專案而言，此每月固定支出違背零成本初衷。

#### 3. 缺乏長輪詢與強制要求公開 HTTPS Webhook
- LINE Messaging API **不支援出站長輪詢 (Long Polling)** 或 WebSocket 連線。
- 若要支援雙向指令互動，必須架設公網可存取、具備合法 CA SSL 憑證之 HTTPS Webhook 伺服器。
- 家用網路環境（浮動 IP、NAT 防火牆）的使用者必須自行設定 DDNS、路由器連接埠轉發（Port Forwarding）或仰賴付費穿透服務（如 ngrok），架設與維護複雜度極高。

#### 4. 因應建議與替代方案
本專案建議使用者採用以下兩大零成本替代方案：
- **推薦方案 A：Telegram Bot**。完全免費、無每月推播上限、出站長輪詢免公網 IP、支援行內按鈕與商品售出動態原位劃線更新。
- **推薦方案 B：Email (SMTP)**。使用個人 Gmail 應用程式密碼即可免費發信（每日約 500 封），支援跨裝置精美 HTML 郵件卡片。

</details>

---

## 日常使用方式

PTT-DC-Bot 同時支援**「個人私訊 (DM)」**與**「伺服器公用頻道」**雙模式：
- **個人私訊專屬追蹤**：直接私訊機器人或在私訊中執行 Slash 指令，推播將直接發送至個人私訊，無需任何伺服器管理權限。
- **伺服器公用頻道推播**：伺服器管理員透過 `/setup` 或 `/channel_set` 綁定頻道，該伺服器設定的所有規則均會推播至指定頻道。

### 1. 自然語言交辦（私訊直接對話 或 `/ai`）

除了視覺化按鈕，你也可以直接像跟真人助理對話一樣交辦需求，AI 會自動辨識意圖並調用後端工具：
- `"幫我追蹤 HardwareSale 板販售的 4070S，預算 18000 元以內"`
- `"我想找台北面交、還有原廠保固的 3060Ti，預算 6500"`
- `"幫我掃描目前 HardwareSale 板上還沒賣掉的 6700XT"`
- `"列出我目前設定的所有追蹤條件"`

### 2. 精確關鍵字語法重點（`/keyword_add`）
- **空白代表 AND**：`台北 3060TI`（標題需同時包含「台北」與「3060TI」）。
- **減號 `-` 代表排除**：`3060 -ti -筆電 -礦`（找純 3060，排除 3060Ti、筆電與礦卡）。
- **預算上限（`max_price`）**：填入金額數值（例如 `6000`），自動排除標價高於該金額的貼文。

### 3. AI 模糊語意追蹤規則（`/fuzzy_add`）
針對無法單純靠標題關鍵字判斷的複雜條件（如：保固期限、盒裝完整度、特定交易地點）：
- `description`：以白話文描述追蹤條件（例如：`RTX 3060Ti 有原廠盒裝且在保固內，非礦卡`）。
- `hint_keywords`（*建議填寫*）：快速初篩關鍵字（例如：`3060,3060ti`），僅在文章標題符合初篩時才調用 LLM 進行深度語意審核，大幅提升處理效率。

### 4. 主動回溯掃描現貨（`/scan_unsold`）
剛設好規則不想枯等新文章？執行 `/scan_unsold keyword: 3060TI`，系統會自動往回檢索近期文章，穿透作者最新推文過濾掉已售出或已結案的貼文，整理成附帶翻頁按鈕的現貨清單。

---

## 進階配置與系統管理（點擊展開）

以下整理完整指令清單、背景守護服務設定、環境變數與內部架構細節：

<details>
<summary><b>1. 完整 25 組 Slash 指令手冊</b></summary>
<br>

#### 視覺化設定與 AI 搜尋
| 指令 | 參數 | 說明 |
| :--- | :--- | :--- |
| `/setup` | `scope` | 開啟視覺化互動設定面板（一鍵綁定頻道、熱門看板下拉選單、新增關鍵字 Modal、測試推播） |
| `/ai` | `prompt`, `scope` | 以自然語言指示 AI 新增規則、查詢看板或回溯搜尋現貨 |
| `/scan_unsold` | `keyword`, `board`, `max_per_keyword` | 回溯掃描看板近期貼文，過濾出尚未售出的商品清單（附翻頁按鈕） |
| `/search` | `keyword`, `board`, `pages` | 呼叫 PTT 原生搜尋查詢歷史文章（不過濾售出狀態） |

#### 精確關鍵字管理
| 指令 | 參數 | 說明 |
| :--- | :--- | :--- |
| `/keyword_add` | `keyword`, `board`, `category`, `max_price` | 新增精確關鍵字（支援空白 AND、`-` 排除詞、預算上限） |
| `/keyword_edit` | `keyword_id`, `new_keyword`, `max_price` | 修改現有關鍵字內容或預算上限（填 0 代表不限金額） |
| `/keyword_remove` | `keyword`, `scope` | 移除指定的精確關鍵字（支援自動補齊選單） |
| `/keyword_list` | `scope` | 檢視目前啟用的所有精確關鍵字、模糊規則與推播狀態 |
| `/keyword_clear` | `scope` | 清空指定範圍內的所有精確關鍵字 |

#### AI 模糊規則與看板管理
| 指令 | 參數 | 說明 |
| :--- | :--- | :--- |
| `/fuzzy_add` | `description`, `board`, `category`, `hint_keywords` | 新增自然語言模糊追蹤規則（依賴語意判讀） |
| `/fuzzy_remove` | `rule`, `scope` | 移除指定的模糊規則 |
| `/fuzzy_list` | `scope` | 檢視目前啟用的模糊規則清單 |
| `/board_add` | `board`, `scope` | 驗證並新增監控的 PTT 看板（建立基準線不洗版） |
| `/board_remove` | `board`, `scope` | 移除指定看板與其關聯規則 |
| `/board_list` | 無 | 查看系統目前正在輪詢的所有 PTT 看板 |

#### 頻道設定、個人狀態與管理
| 指令 | 參數 | 說明 |
| :--- | :--- | :--- |
| `/channel_set` | `channel`, `scope` | 設定推播通知的目標頻道（私訊中執行則設為私訊通知） |
| `/pause` | `scope` | 暫停接收推播通知（保留既有規則設定） |
| `/resume` | `scope` | 恢復接收推播通知 |
| `/me` | 無 | 查看個人目前的追蹤規則、私訊連線狀態與今日剩餘配額 |
| `/forget_me` | 無 | 徹底刪除個人在資料庫中的所有規則、推播紀錄與對話記憶 |
| `/status` | `scope` | 檢視系統運行狀態、Groq 佇列負載與爬蟲狀態 |
| `/check_now` | 無 | 立即手動觸發一次看板巡檢（非管理員具備 5 分鐘冷卻） |
| `/push_mode` | `mode` (`keyword` / `all`) | 切換關鍵字匹配或全看板新文推播（限管理員） |
| `/admin` | `action`, `target_id`, `tier` | 系統擁有者專用：檢視全局統計、調整用戶配額階層或黑名單 |
| `/help` | 無 | 顯示指令摘要與快速操作說明 |

</details>

<details>
<summary><b>2. 背景守護常駐行程（Linux systemd 與 tmux）</b></summary>
<br>

若偏好在 Linux 虛擬主機或實體機以原生 Python 運行，專案提供兩種守護常駐方式：

#### (A) 使用 Linux systemd 系統服務（推薦給長期營運主機）
專案目錄已內建 `ptt-bot.service` 範本：
```bash
# 1. 複製服務檔至系統目錄
sudo cp ptt-bot.service /etc/systemd/system/ptt-bot.service

# 2. 編輯服務檔，修改 User 與 WorkingDirectory 為您的實際專案路徑
sudo nano /etc/systemd/system/ptt-bot.service

# 3. 重新載入設定並啟用開機自動啟動
sudo systemctl daemon-reload
sudo systemctl enable --now ptt-bot

# 4. 檢查服務狀態與日誌
sudo systemctl status ptt-bot
journalctl -u ptt-bot -f
```

#### (B) 使用 tmux 守護腳本
若主機具備 `tmux`，可使用內建的管理腳本（程序異常中斷時會於 10 秒後自動重啟）：
```bash
# 啟動 tmux 背景常駐服務 (Session: ptt_bot)
bash start_bg.sh

# 查看即時日誌
tail -f bot.log

# 停止服務
bash stop_bg.sh

# 重啟服務
bash restart_bg.sh
```

</details>

<details>
<summary><b>3. 完整 .env 環境變數參數說明</b></summary>
<br>

| 變數名稱 | 必填 | 預設值 | 說明 |
| :--- | :---: | :--- | :--- |
| `BOT_LANGUAGE` | 否 | `zh_TW` | 機器人語言偏好設定（`zh_TW` 繁體中文 / `en` 英文） |
| `DEFAULT_BOARD` | 否 | `HardwareSale` | 預設監控看板名稱 |
| `POLL_INTERVAL_MIN` | 否 | `90` | 背景巡檢最小間隔秒數 |
| `POLL_INTERVAL_MAX` | 否 | `150` | 背景巡檢最大間隔秒數（隨機抖動避免固定頻率請求） |
| `ENABLE_DISCORD` | 否 | `true` | 是否啟用 Discord 推播平台 |
| `DISCORD_TOKEN` | 條件必填 | 無 | Discord Bot 認證 Token（啟用 Discord 時必填） |
| `DEFAULT_CHANNEL_ID` | 否 | 空 | 預設 Discord 推播頻道 ID（亦可於頻道使用 `/setup` 綁定） |
| `DEFAULT_GUILD_ID` | 否 | 空 | 主伺服器 ID（擁有較高配額階層） |
| `OWNER_USER_ID` | 否 | 空 | 管理員 Discord User ID（留空時自動從 Bot Application 解析） |
| `ENABLE_TELEGRAM` | 否 | `false` | 是否啟用 Telegram Bot 推播平台 |
| `TELEGRAM_BOT_TOKEN` | 條件必填 | 空 | Telegram Bot HTTP API Token（啟用 Telegram 時必填） |
| `TELEGRAM_CHAT_ID` | 條件必填 | 空 | 預設 Telegram 推播目標 Chat ID（個人 ID、群組負數 ID 或 `@channel`） |
| `TELEGRAM_ADMIN_USER_ID` | 否 | 空 | Telegram 管理員 User ID |
| `ENABLE_EMAIL` | 否 | `false` | 是否啟用 Email SMTP 郵件推播平台 |
| `SMTP_HOST` | 條件必填 | `smtp.gmail.com` | SMTP 伺服器主機位址（啟用 Email 時必填） |
| `SMTP_PORT` | 否 | `587` | SMTP 伺服器連接埠（STARTTLS 預設 587，SSL 預設 465） |
| `SMTP_USER` | 條件必填 | 空 | SMTP 認證帳號 / Gmail 帳號（啟用 Email 時必填） |
| `SMTP_PASSWORD` | 條件必填 | 空 | SMTP 認證密碼 / Gmail 16 碼應用程式密碼（啟用 Email 時必填） |
| `SMTP_USE_TLS` | 否 | `true` | 是否啟用 STARTTLS 加密通道（Port 465 時建議設為 false 採 SSL） |
| `EMAIL_FROM` | 否 | 同 `SMTP_USER` | 寄件者電子郵件地址 |
| `EMAIL_FROM_NAME` | 否 | `PTT 智慧追蹤機器人` | 寄件者顯示名稱 |
| `EMAIL_TO` | 條件必填 | 空 | 收件者電子郵件地址（支援逗號分隔多組信箱，啟用 Email 時必填） |
| `GROQ_API_KEY` | 否 | 無 | Groq Cloud API 金鑰（用於自然語言對話與售出語意審核，選填） |
| `GROQ_MODEL` | 否 | `openai/gpt-oss-120b` | 主力語意分析模型 |
| `GROQ_FAST_MODEL` | 否 | `openai/gpt-oss-20b` | 輕量快速初篩模型 |
| `DB_PATH` | 否 | `bot_data.db` | SQLite 資料庫路徑（Docker 容器內預設為 `/app/data/bot_data.db`） |
| `LOG_PATH` | 否 | `bot.log` | 日誌檔案路徑（5MB 自動輪替保留 3 份） |

</details>

<details>
<summary><b>4. 系統內部架構與目錄結構</b></summary>
<br>

```text
ptt-dc-bot/
├── bot.py                  # Discord Client 主程式、/setup 視覺化面板與協調器
├── telegram_bot.py         # Telegram Client 主程式、長輪詢迴圈與雙向指令模組
├── email_notifier.py       # Email SMTP 非阻塞非同步郵件推播供應商模組
├── delivery.py             # 抽象 NotificationProvider 與多平台 DeliveryDispatcher
├── setup_wizard.py         # 多平台零依賴互動式 CLI 設定精靈（標準庫實作）
├── install.sh              # Linux / macOS 一鍵安裝與設定腳本
├── install.ps1             # Windows PowerShell 一鍵安裝腳本
├── start_windows.bat       # Windows 雙擊啟動器（含 10 秒自動重啟守護）
├── start_mac.command       # macOS 雙擊啟動器
├── config.py               # 多平台環境變數載入、租戶配額常數與輸入過濾規則
├── storage.py              # SQLite WAL 多租戶資料庫 (Schema v4)、平台欄位支援
├── ptt_crawler.py          # 非同步 PTT 爬蟲、雙層售出判定與多品項獨立視角切分
├── groq_scheduler.py       # Groq API 優先級佇列（P0~P3）與三模型動態負載池
├── groq_agent.py           # ReAct AI 代理、工具調用、權限門禁與 Prompt Injection 防護
├── embeds.py               # Discord Embed 卡片建構、字數邊界截斷與防搶翻頁按鈕
├── docs/
│   ├── MULTI_PLATFORM_FEASIBILITY.md # LINE, Telegram, Email 多平台調研評估報告
│   └── RELEASE_NOTES_v1.0.0.md       # v1.0.0 正式版 Release Notes
├── Dockerfile              # Python 3.11-slim 輕量容器映像檔定義（以非 root botuser 運行）
├── docker-compose.yml      # 容器編排（支援 GHCR 預建映像檔與本地編譯雙模式）
├── .dockerignore           # 26 組排除規則，杜絕金鑰、日誌與快取被打包至映像檔
├── .github/workflows/      # release.yml CI/CD 測試、跨平台壓縮包與 GHCR 發布工作流
└── tests/                  # 100% 離線 Mock 單元測試與安全性回歸測試套件
```

**背景巡檢流程（Crawl-Once, Fan-Out）**：
1. **聚合活躍看板**：僅輪詢具備有效訂閱規則的看板，無規則看板自動略過。
2. **單次爬取共享**：每個看板每輪僅發送一次 HTTP 請求，內文快取（10 分鐘）與 AI 判定快取（60 分鐘）全租戶共享，徹底避免高頻請求引發 PTT 503 限流。
3. **多品項視角切分**：透過 `apply_keyword_view` 為合售文中的不同關鍵字建立虛擬品項視角，獨立計算售價與個別售出狀態。
4. **多平台解耦派發**：透過 `DeliveryDispatcher` 將匹配的文章分發至已註冊之各平台 Provider（Discord、Telegram、Email），彼此隔離不互相阻塞。
5. **18h 原位追蹤**：背景輪詢過去 18 小時內推播過的文章，一旦作者推文或標題標註售出，立即於 Discord 與 Telegram 原位編輯通知訊息加上刪除線。

</details>

<details>
<summary><b>5. Groq 多模型調度器與配額階層設計</b></summary>
<br>

Groq 免費方案對單一模型具備每分鐘 Token 數（TPM）與每日請求數（RPD）限制。為防止背景爬蟲與使用者互動互相排擠，`groq_scheduler.py` 實作了專門調度機制：

- **三模型動態負載池**：輪替調度 `openai/gpt-oss-120b`、`qwen/qwen3.8-27b` 與 `openai/gpt-oss-20b` 三個獨立模型桶，可用免費額度擴充至 3 倍（合計約 24,000 TPM）。
- **四級優先佇列**：
  - `P0 (Interactive)`：使用者私訊與 `/ai` 即時互動（最高優先級）。
  - `P1 (Realtime)`：背景新文章推播的語意審核。
  - `P2 (Batch Scan)`：`/scan_unsold` 批次現貨掃描（模型可用額度低於 30% 時自動降速讓位給 P0/P1）。
  - `P3 (Maintenance)`：低優先級背景維護作業。

**預設租戶配額表**（背景自動推播不扣除個人的每日對話或掃描額度）：

| 配額項目 | 個人私訊 (`friend`) | 一般伺服器 (`free`) | 管理員 / 主伺服器 (`owner`) |
| :--- | :---: | :---: | :---: |
| 精確關鍵字上限 | 30 組 | 10 組 | 999 組 |
| AI 模糊規則上限 | 8 組 | 3 組 | 999 組 |
| 同時追蹤看板數 | 5 個 | 5 個 | 20 個 |
| 每日 `/ai` 與私訊對話 | 60 次 / 日 | 20 次 / 日 | 無限制 |
| 每日 `/scan_unsold` 掃描 | 25 次 / 日 | 8 次 / 日 | 無限制 |

</details>

<details>
<summary><b>6. 安全性防護設計（Prompt Injection / RBAC / SSRF / 容器加固）</b></summary>
<br>

針對於公開 Discord 伺服器部署可能面臨的惡意利用，專案實作了嚴格的縱深防禦：
- **Prompt Injection 隔離**：所有爬取自外部的 PTT 內文均經過 `escape_prompt_xml` 轉義處理並封裝於 `<untrusted_ptt_content>` 標籤中；工具調用層設有**意圖鎖定機制（Intent Guard）**，外部文章若暗藏指令，無法誘使 AI 擅自調用規則修改或刪除工具。
- **多租戶 RBAC 權限隔離**：一般伺服器成員無權修改伺服器共用推播設定；資料庫更新與刪除操作一律強制綁定 `subscriber_id`，防止跨租戶存取。
- **SSRF 與查詢防護**：爬蟲嚴格校驗目標 URL 必須為 `https://www.ptt.cc/bbs/` 官方路徑，並全面停用 HTTP 重定向（`allow_redirects=False`）；SQLite 全面使用參數化查詢與 `LIKE ... ESCAPE '/'`。
- **錯誤訊息脫敏與最小權限**：前端異常訊息全面隱藏內部實體路徑與 Traceback；Docker 容器以獨立使用者（UID 1000 `botuser`）運行、應用程式目錄唯讀，並移除所有 Linux 特權（`cap_drop: ALL`）。

</details>

<details>
<summary><b>7. 常見問題與排錯（FAQ）</b></summary>
<br>

**Q1：在私訊設定了追蹤規則，但為什麼一直沒收到推播？**  
請先輸入 `/me` 檢查狀態。如果顯示「曾發生私訊失敗」，代表您的 Discord 隱私設定阻擋了機器人的私訊。請至 Discord「使用者設定 -> 隱私與安全」開啟「允許來自伺服器成員的私人訊息」，接著隨意發送一則訊息給機器人，推播就會自動恢復。

**Q2：為什麼在伺服器頻道輸入指令會顯示「權限不足」？**  
在伺服器頻道中輸入指令預設為修改「整個伺服器的共用追蹤規則」，需要具備伺服器管理員權限（`Manage Server` 或 `Manage Channels`）。若您是一般成員想設定個人追蹤，請**直接私訊機器人**輸入指令，或在指令中將 `scope` 參數指定為 `個人專屬追蹤 (user)`。

**Q3：為什麼某些文章符合關鍵字卻沒有推播？**  
系統會主動過濾符合以下條件的文章：
1. 發文時間超過 21 天的舊文。
2. 標題或作者推文已明確標註已售出/暫售/洽中。
3. 同一賣家於 3 天內重複張貼的相同品項（防洗版去重）。
4. 內文標價高於您設定的預算上限 `max_price`。  
*（若欲查詢歷史所有貼文包含已售出商品，請改用 `/search` 指令）。*

</details>

---

## 離線單元測試與回歸驗證

本專案具備完整的離線單元測試套件（使用本地記憶體/臨時 SQLite 資料庫與 Mock 物件，無需真實連網或填入真實 API 金鑰即可執行）：

```bash
# 執行全量離線單元與回歸測試
LOG_PATH=/tmp/test.log DB_PATH=/tmp/test.db PYTHONPYCACHEPREFIX=/tmp/pycache PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover tests
```

測試涵蓋設定精靈、Token 離線驗證、OAuth2 URL 計算、跨平台雙擊啟動器語法、Discord `/setup` 互動 UI、CI/CD 發布設定與安全性邊界。

---

## 致謝與聲明 (Acknowledgements & Disclaimer)

本專案之核心架構、PTT 爬蟲與業務過濾邏輯由開發者主導設計完成；在開發與發布準備過程中，藉由 Google Gemini 模型的輔助進行程式碼重構、安全性滲透防禦加固、跨平台部署自動化腳本編寫與中英雙語文件在地化。

