# PTT-DC-Bot v1.0.0 正式版發行說明 (Release Notes)

[版本] v1.0.0 (Official General Availability)  
[發行日期] 2026-11-08  
[標籤物件] `refs/tags/v1.0.0`  
[品質閘門] 134 / 134 單元與回歸測試 100% 通過 (通過率 100%, 0 回歸, 0 錯誤)  
[授權條款] MIT License  

---

## 1. [摘要] 發行總結

PTT-DC-Bot 正式推出首個穩定生產版本 **v1.0.0**！

本專案專為解決 PTT 二手交易與熱門看板（如 `HardwareSale` 電蝦二手、`MacShop` 蘋果二手、`nb-shopping` 筆電交易、`Gamesale` 遊戲交易、`MobileComm` 手機通訊）長期以來的推播痛點而設計。突破傳統推播腳本僅做單向文字比對的限制，PTT-DC-Bot 深度結合「規則過濾引擎」與「Groq 免費雲端 LLM 語意推論」，實現了**已售出原位編輯刪除線**、**多品項合售獨立解析**、**型號數字與價格智慧消歧義**，以及**伺服器公用與個人私訊多租戶隔離**等多項獨家機制。

在經歷嚴格的 Pre-release 全面驗證審計（包含三大部署方式、雙語系本地化、真實 Discord 網關連線、PTT 線上即時爬取 100% 零遺漏對比）後，本版本正式標記為生產環境就緒（Production-Ready）。

---

## 2. [核心功能] 核心架構與功能亮點

### 2.1 二手交易痛點終結者
1. **[已售出自動原位更新 (In-place Update)]**  
   推播後持續追蹤貼文 18 小時，穿透至簽名檔下方檢查作者本人最新推文（如「已售出」、「暫售」、「洽中」）。一旦確認售出，直接透過 Discord REST API 編輯原推播訊息卡片加上刪除線，徹底避免洗版並防止使用者白高興點入已售出文章。
2. **[多品項合售文獨立解析]**  
   針對一篇貼文內同時販售多件商品之情境（例如：`1. GTX 1060 (已售出) / 2. RTX 3060Ti $5800`），依據不同關鍵字切分獨立上下文視角，個別判斷售出狀態與對應售價，不因單一品項售出而誤殺整篇合售文。
3. **[硬體型號與價格消歧義]**  
   內建型號智慧遮蔽規則，排除標題與內文中的 `RTX 3060`、`12G`、`750W`、`i5-12400` 等硬體規格數字干擾，精準提取真實交易金額；支援 `5.8k`、`6k` 等常見口語縮寫自動換算與使用者預算上限過濾。
4. **[歷史現貨回溯搜尋 (/scan_unsold)]**  
   支援直接回溯掃描看板上歷史文章，即時過濾並整理目前尚未售出的現貨清單，方便買家快速尋找目標二手硬體。

### 2.2 智慧語意與多模型輪替架構
1. **[免付費 Groq LLM 自然語言交辦]**  
   支援直接以白話文交辦複雜過濾條件（例如：「幫我追 6500 元以內、有原廠保固的 3060Ti」），由 Groq 雲端高速模型進行語意審核與資訊提取。
2. **[三模型動態輪替池與優先級佇列]**  
   整合 Groq 免費模型池，建構動態權重輪替機制（合計 24,000 TPM），搭配 P0~P3 優先級非同步佇列調度，確保大規模背景巡檢穩定運作且永不觸發 Rate Limit。
3. **[Prompt Injection 嚴格隔離]**  
   對爬取之 PTT 外部未受信任內文採用 `<untrusted_ptt_content>` 邊界隔離標籤，杜絕提示詞注入與惡意引導攻擊。

### 2.3 多租戶與全域互動
1. **[SQLite WAL 多租戶架構]**  
   採用 SQLite3 搭配 WAL (Write-Ahead Logging) 模式與執行緒安全 RLock，支援單一 Bot 實例同時服務多個 Discord 伺服器公用頻道與多位使用者的個人私訊（DM），各自的推播規則與配額完全獨立隔離。
2. **[全域 Slash 指令樹 (25 個指令)]**  
   支援完整的 Slash 指令操作（`/setup`, `/keyword_add`, `/keyword_list`, `/board_add`, `/scan_unsold`, `/ai` 等），並提供看板智慧中文別名（例如：輸入「硬體買賣」自動對應 `HardwareSale`）與自動補齊功能。
3. **[單次爬取多租戶共享分發 (Crawl-Once, Fan-Out)]**  
   背景爬蟲對同一看板僅進行單次請求並快取解析結果，瞬間分發給所有符合條件的租戶，對 PTT 伺服器維持極低負載與友善請求頻率。

---

## 3. [部署與發行] 三大跨平台部署方案

為滿足不同使用者的伺服器環境與維運習慣，v1.0.0 提供三種經過 100% 驗證的部署方式：

### 方案 A：一行指令自動化安裝（推薦首選）
- **Linux / macOS**:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.sh | bash
  ```
- **Windows (PowerShell)**:
  ```powershell
  irm https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.ps1 | iex
  ```
- 自動偵測 Python 3.10+、建立虛擬環境、安裝相依套件、自動引導終端設定精靈並啟動服務。

### 方案 B：GitHub Releases 雙擊即開免安裝包
- 前往 GitHub Releases 下載對應作業系統的預先打包歸檔包：
  - Windows: `ptt-dc-bot-v1.0.0-windows.zip`（雙擊 `start_windows.bat`）
  - macOS: `ptt-dc-bot-v1.0.0-macos.tar.gz`（雙擊 `start_mac.command`）
  - Linux: `ptt-dc-bot-v1.0.0-linux.tar.gz`（執行 `./start_bg.sh` 背景守護啟動）
- Windows 啟動器內建 10 秒自動守護迴圈，遭遇網路波動自動嘗試恢復。

### 方案 C：企業級 Docker Compose 容器化部署
- 預先建置 Multi-Arch 映像檔，發佈至 GitHub Container Registry (`ghcr.io`)：
  ```bash
  docker compose up -d
  ```
- **資安加固特性**：
  - 非 root 帳號執行 (`uid=1000(botuser)`)。
  - 核心程式碼唯讀掛載 (`0755 root:root`)。
  - 獨立資料卷持久化 (`./data:/app/data`，`0700 botuser:botuser`)。
  - 最小化 Linux 權限剝離 (`cap_drop: [ALL]`) 與防提權 (`no-new-privileges:true`)。
  - 內建非同步 Healthcheck 探針。

---

## 4. [變更歷程] 詳細版本變更日誌

### [新增] 功能與使用者體驗
- **[核心]** 實作 PTT 爬蟲引擎，支援反爬蟲指數退避機制與 18 歲年齡驗證 Cookie。
- **[推播]** 實作 `DiscordProvider` 與 `DeliveryDispatcher`，支援 Discord 豐富 Embed、按鈕元件與原位編輯更新。
- **[AI]** 整合 Groq LLM 語意推論引擎與三模型輪替池（P0-P3 排程器）。
- **[UX]** 支援看板中文別名對照（`硬體買賣`、`筆電買賣`、`遊戲交易` 等）與 Slash 指令自動補齊。
- **[雙語系]** 支援繁體中文 (`zh_TW`) 與英文 (`en`) 雙語系設定精靈與 UI 向量面板資源。
- **[設定精靈]** 實作互動式 `setup_wizard.py`，連線驗證 Discord Token 與 Groq API Key，並自動鎖定 `.env` 檔案權限為 POSIX 0600。

### [優化] 穩定性與系統效能
- **[CI/CD]** 建立 GitHub Actions 發行管線，涵蓋 Python 3.10~3.13 矩陣測試、Shell 靜態語法檢查、跨平台歸檔發行與 GHCR 容器推送。
- **[維運]** 提供 Linux 背景常駐守護腳本（`start_bg.sh`, `stop_bg.sh`, `restart_bg.sh`）與 systemd 服務範本（`ptt-bot.service`）。
- **[視覺風格]** 全面移除程式碼與 Embed 中的裝飾性 Emoji，採用結構化方括號標籤，建立專業視覺規範。

### [修復] 相容性與例外處理
- **[依賴]** 修復 Python 3.13 環境下 `audioop-lts` 相依性限制。
- **[CI]** 補齊 PyYAML 依賴與測試環境 PYTHONPATH 路徑設定。
- **[防護]** 強化 SSRF 防護與外部輸入檢查，阻絕非 PTT 網域之爬取請求。

---

## 5. [驗證報告] 品質審計與測試證明

在發行本正式版本前，團隊執行了基準全面審計驗證（Forensic Verification），取得 100% 合規成績：
1. **[單元與回歸測試]**  
   執行全部 134 項測試，結果：`134 passed, 0 failed, 0 errors in 0.557s`（100% 通過率）。
2. **[真實網關推播驗證]**  
   使用測試機器人登入 Discord Gateway，成功同步 25 個全域 Slash 指令，並順利向真實頻道發送推播訊息（Message ID: `1568645163247476808`，REST API 驗證 HTTP 200）。
3. **[PTT 線上即時真值比對 (Ground-Truth)]**  
   直接對比 PTT Web 線上真實貼文列表與爬蟲抓取結果，6 篇活躍文章達到 **100% 欄位一致、0 篇遺漏 (0% omission rate)**。
4. **[售出偵測與價格過濾]**  
   成功驗證賣家於內文/推文更新「暫售」之標記攔截，並精準過濾負向排除詞與價格預算門檻。
5. **[冷啟動持久化]**  
   經 SIGTERM / SIGINT 中斷後重新啟動，SQLite WAL 資料庫無任何損毀（`PRAGMA integrity_check` -> `ok`），訂閱頻道、規則與推播歷史 100% 保留。

---

## 6. [升級指南] 現有使用者升級說明

### 6.1 由 Beta 版或既有 Git 倉庫升級
```bash
git checkout main
git pull origin main
source .venv/bin/activate  # 或 Windows: .venv\Scripts\activate
pip install -r requirements.txt --upgrade
python3 bot.py  # 或執行重啟腳本: ./restart_bg.sh
```

### 6.2 Docker 容器使用者升級
```bash
docker compose pull
docker compose up -d
```

### 6.3 資料庫相容性
本版本資料庫結構維持完全向下相容，既有的 `bot_data.db` 將由程式自動偵測並平滑過渡，無需手動執行 SQL 遷移腳本。

---

## 7. [致謝] 專案維護

感謝所有提供測試回饋、看板建議與通報異常的使用者。  
如在使用過程中發現問題或有新功能建議，歡迎至專案 GitHub Issues 提出討論回報！
