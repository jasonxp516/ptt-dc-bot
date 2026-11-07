# PTT 智慧追蹤機器人 - 多平台擴充可行性調研與技術架構評估報告

[文件版本] v1.0.0
[建立日期] 2026-11-08
[目標分支] beta
[適用模組] bot.py, delivery.py, telegram_bot.py, email_notifier.py, setup_wizard.py

---

## 1. 執行摘要 (Executive Summary)

PTT 智慧追蹤機器人（PTT Bot）在 v1.0.0 正式版中已具備完整的 Discord 推播、Slash 指令互動與商品售出狀態自動更新功能。為提升跨情境適用性與服務韌性，本專案在 `beta` 分支針對常見通訊與通知平台展開全面之技術與成本可行性評估：

1. **LINE 平台**：[限制不支援] 因 LINE Notify 於 2025 年 3 月 31 日正式終止服務，且替代方案 LINE Messaging API 每月免費額度僅 200 則主動推播（超過即中斷，超額月費高達 NT$800+），並強制要求公網 HTTPS Webhook，不符合開源個人自架之「零成本」與「開箱即用」原則，判定**現階段不具實作可行性**。
2. **Telegram 平台**：[建議實作] 完全零成本、無每月推播上限、支援長輪詢（Long Polling）可無縫穿透家用 NAT/防火牆、支援豐富之行內鍵盤（Inline Keyboard）與訊息即時編輯（In-Place Edit for Sold Status），為最理想之雙向互動平台。
3. **Email SMTP 平台**：[建議實作] 零成本（支援 Gmail 應用程式密碼）、通用跨裝置、支援響應式 HTML 卡片排版，並透過 Python 內建標準庫 `smtplib` 與 `asyncio.to_thread` 達成零新增外部相依，為極佳之異步即時通知平台。

---

## 2. LINE 平台深度調研與限制剖析

### 2.1 LINE Notify 正式終止服務
- **終止時間點**：LINE 官方公告於 **2025 年 3 月 31 日** 正式結束 LINE Notify 服務。
- **影響範圍**：自 2025 年 4 月 1 日起，所有對 `https://notify-api.line.me/api/notify` 之 HTTP 請求全面失效（HTTP 410 Gone / 404 Not Found），使用者無法再透過 Access Token 發送任何免費通知。此服務落日為不可逆之官方政策。

### 2.2 LINE Messaging API 額度與成本障礙
- **免費額度限制**：LINE 官方帳號（LINE Official Account）免費方案（Communication / 輕用量）**每月僅提供 200 則免費主動訊息（Push Message / Multicast）**。
- **計費邏輯與快速耗盡問題**：
  - 用戶主動發送指令之即時回應（`replyMessage`）雖然免費，但監控推播本質上為**非同步主動發送（`pushMessage`）**。
  - 推播計費按「人次」累計：若單一機器人設定 3 個訂閱者，每推播 1 篇文章即消耗 3 則額度。
  - 以 PTT 硬體買賣板（HardwareSale）或省錢板（Lifeismoney）為例，單日命中文章數常達 10~20 篇。單人使用下，**最快第 10 天即用盡整月額度**；多使用者環境下更在數日內全面癱瘓。
- **超額封鎖與升級費用**：
  - 免費方案**不支援超量發送**，一旦達到 200 則上限，後續所有推播直接拋出 HTTP 429 或額度不足錯誤，直到次月 1 號重置。
  - 付費方案門檻高：中用量方案（Light）約 NT$800/月（5,000 則），高用量方案（Standard）約 NT$1,200/月（30,000 則）。對於開源自架工具而言，每月固定支出違背專案初衷。

### 2.3 網路連線與架構限制
- **缺乏出站長輪詢機制**：LINE Messaging API **不支援** Long Polling 或出站 WebSocket 連線。
- **強制公開 HTTPS Webhook**：
  - 雙向事件接收必須提供公網可存取的 Webhook URL。
  - 該 URL 必須具備受信任憑證機構簽發之 SSL 憑證（HTTPS）。
  - 對於多數運行於家庭內部網路（NAT、浮動 IP、非固定寬頻）之使用者，必須額外設定 Port Forwarding、DDNS，或依賴第三方穿透工具（如 ngrok、Cloudflare Tunnel），維護複雜度與故障率極高。

### 2.4 決策結論
基於額度硬性上限、高額月費與網路架構門檻，本專案在當前版本**明確排除 LINE 平台之推播實作**，並於文件清楚說明技術決策背景，引導使用者採用 Telegram 或 Email 方案。

---

## 3. Telegram Bot 平台優勢與技術架構

### 3.1 平台優勢綜整
1. **完全零成本與無推送上限**：
   - 透過官方 @BotFather 建立機器人完全免費。
   - 官方未設定每月推播上限，僅受基本速率限制（單一對話 1 msg/s，全域廣播 30 msg/s），足以應對高頻 PTT 監控需求。
2. **長輪詢（Long Polling）極簡網路架構**：
   - 支援 `getUpdates` 長輪詢模式，由本機直接向 Telegram 官方伺服器（`api.telegram.org:443`）發起出站 HTTPS 請求。
   - **無需公網 IP、無需網域名稱、無需 SSL 憑證、無需任何路由器連接埠映射**，真正做到隨開即用。
3. **優異的雙向互動性**：
   - 支援文字斜線指令（`/start`, `/help`, `/keyword_add`, `/keyword_list`, `/pause`, `/resume` 等）。
   - 支援行內鍵盤選單（`InlineKeyboardMarkup` / `InlineKeyboardButton`），提供「前往 PTT 原文閱讀」按鈕與一鍵刪除規則按鈕。
4. **商品售出狀態動態更新（In-Place Edit）**：
   - 支援 `editMessageText` API，當文章標題更新為 `[已售出]` 時，機器人可直接在原訊息上加入劃線刪除與售出標籤，保持通知版面整潔，體驗與 Discord 完全對齊。
5. **零新增第三方套件依賴**：
   - Telegram Bot API 本質為乾淨的 HTTP REST API，專案既有之 `aiohttp` 即可無縫封裝非同步客戶端，無需引進龐大的第三方庫。

### 3.2 訊息長度與格式化規範
- Telegram 單則訊息上限為 4,096 字元。
- 採用安全 HTML 格式化模式（`parse_mode='HTML'`），內文預覽截斷至 500~800 字元以內，並對標題與作者進行 `html.escape` 轉義，防止特殊符號破壞標籤結構。

---

## 4. Email SMTP 平台優勢與技術架構

### 4.1 平台優勢綜整
1. **高通用性與零成本**：
   - 基於網際網路標準 SMTP 協定，不受限於任何專屬通訊軟體生態。
   - 支援 Gmail「應用程式密碼」（16 碼 App Password），個人 Gmail 每日提供約 500 封免費發信額度，足以負擔個人與小群組重點商品追蹤。
2. **豐富之 Responsive HTML 卡片排版**：
   - 採用 MIME `multipart/alternative` 同時產生純文字備援與精美 HTML 郵件。
   - HTML 版面包含看板徽章、標題超連結、作者、價格高亮標籤、內文重點摘錄與 CTA 按鈕。
3. **零外部依賴之非同步非阻塞發送**：
   - 使用 Python 標準函式庫 `smtplib` 與 `email.message.EmailMessage`。
   - 透過 `asyncio.to_thread` 委派同步 SMTP 連線與發信至背景執行緒池，避免阻塞 asyncio 事件迴圈，同時無需引入 `aiosmtplib` 等額外依賴。
4. **安全防護（Anti-XSS）**：
   - 所有爬取自 PTT 之使用者輸入（標題、內文、作者）於組裝 HTML 時均經嚴格之 HTML 實體轉義（`html.escape`），杜絕郵件客戶端跨站腳本風險。

---

## 5. 四大通訊與通知平台綜合比較矩陣

| 評估維度 | Discord (現行正式版) | Telegram Bot (Beta 新增) | Email SMTP (Beta 新增) | LINE Messaging API (受限不支援) |
| :--- | :--- | :--- | :--- | :--- |
| **運作成本** | [成功] 完全免費 | [成功] 完全免費 | [成功] 完全免費 (Gmail/個人伺服器) | [錯誤] 每月僅 200 則免費，超額 NT$800+/月 |
| **連線機制** | WebSocket Gateway (出站) | Long Polling (出站) | SMTP TCP (出站) | [錯誤] 雙向必須提供公網 HTTPS Webhook |
| **NAT/家用穿透** | [成功] 無需公網 IP / 免憑證 | [成功] 無需公網 IP / 免憑證 | [成功] 無需公網 IP / 免憑證 | [錯誤] 需公網固定 IP、網域、SSL 或反向穿透 |
| **推播數量限制** | 無硬性上限 (遵循 API Rate Limit) | 無硬性上限 (30 msg/s 全域) | 每日約 500 封 (Gmail 限制) | [錯誤] 每月僅 200 則主動推播上限 |
| **雙向互動能力** | 原生 Slash 指令、按鈕、下拉選單 | 文字斜線指令、行內按鈕選單 | 僅單向推播 (雙向需另建 IMAP 輪詢) | 需自行撰寫 Webhook 處理伺服器 |
| **售出動態更新** | [成功] 支援 (原訊息編輯) | [成功] 支援 (`editMessageText`) | [警告] 不支援 (已發送信件不可竄改) | [警告] 僅能另發推播更新 (加速耗盡額度) |
| **額外相依套件** | `discord.py` | [成功] 零新增 (沿用 `aiohttp`) | [成功] 零新增 (內建 `smtplib`) | 需新增 HTTP Webhook 伺服器套件 |
| **主要適用場景** | 伺服器社群廣播、個人私訊通知 | 個人即時盯梢、跨平台群組通知 | 關鍵重點文章備份、低頻郵件提醒 | 現階段不建議使用 |
| **可行性結論** | **正式版已支援** | **極佳 (Beta 優先實作)** | **極佳 (Beta 優先實作)** | **不可行 (技術與成本限制)** |

---

## 6. 架構整合與實作路徑 (Roadmap)

1. **分發層與平台解耦 (`delivery.py`)**：
   - 定義抽象基底類別 `BaseNotificationProvider`，規範 `deliver()` 與 `update_sold()` 方法。
   - 建立 `DeliveryDispatcher` 依據訂閱者之 `platform` 屬性動態分流至 `DiscordProvider`、`TelegramProvider` 或 `EmailProvider`。
2. **資料庫結構升級 (`storage.py` Schema v4)**：
   - 升級 `subscribers` 資料表，新增 `platform` 與 `target_id` 欄位，確保向下相容既有 Discord 訂閱者。
3. **初次設定精靈擴充 (`setup_wizard.py`)**：
   - 新增跨平台互動選單，支援 Discord、Telegram、Email 設定與驗證測試。
4. **文件更新與摺疊導引 (`README.md` & `README_EN.md`)**：
   - 於文件開頭加入跨平台比較表，並將各平台之 Token 取得與設定指引以 HTML `<details><summary>` 標籤進行摺疊呈現。
