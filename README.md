# PTT-DC-Bot

[English](README_EN.md) | **繁體中文**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![Groq API](https://img.shields.io/badge/LLM-Groq%20API-f55036)](https://groq.com/)
[![Docker Image](https://img.shields.io/badge/Docker-GHCR%20Multi--Arch-2496ED?logo=docker&logoColor=white)](https://github.com/jasonxp516/ptt-dc-bot/pkgs/container/ptt-dc-bot)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

結合規則過濾與 Groq LLM 語意判讀的 PTT 看板監控與多平台推播機器人，支援 Discord、Telegram 與 Email SMTP。專為 `HardwareSale`、`MacShop`、`nb-shopping`、`Gamesale` 等二手交易看板設計，解決已售出誤報、合售文混淆與型號誤判價格等痛點。

---

## 核心特色

1. **已售出自動原位劃線更新（In-Place Edit）**  
   推播後持續追蹤 18 小時，穿透檢查作者最新推文（如「已售出」、「洽中」），確認售出直接在原 Discord / Telegram 訊息卡片加上刪除線，不重複洗版。
2. **多品項合售文獨立視角切分**  
   一篇貼文同時販售多樣商品時，自動依關鍵字拆分獨立虛擬品項，各自解析售出狀態與對應價格。
3. **硬體型號與價格消歧義**  
   排除 `RTX 3060`、`12G`、`750W`、`i5-12400` 等型號數字干擾，支援 `k` 單位換算與精準預算上限過濾。
4. **Crawl-Once, Fan-Out 高效架構**  
   每輪每個看板僅需單次爬取，全租戶共享文章與 AI 快取，杜絕頻繁請求觸發 PTT 503 限流。
5. **單一實例支援伺服器共用與個人私訊（DM）**  
   SQLite WAL 多租戶隔離，伺服器公用頻道與成員個人私訊追蹤各自獨立，互不干擾。

---

## 一行快速部署

打開終端機，執行以下一行指令即可自動完成安裝、相依套件配置與互動設定精靈：

### Linux / macOS
```bash
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.sh | bash
```

### Windows (PowerShell)
```powershell
irm https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.ps1 | iex
```

> **進階部署指南**：
> - [Docker Compose 與 GHCR 容器部署](docs/deploy-docker.md)
> - [Synology DSM (Container Manager) 部署](docs/deploy-synology.md)
> - [unRAID Docker 範本部署](docs/deploy-unraid.md)
> - [Linux systemd 與 tmux 背景守護常駐](docs/deploy-linux.md)
> - [隱私政策與資料自主刪除說明](PRIVACY.md)

---

## 視覺化操作與截圖

機器人提供開箱即用的原生 `/setup` 視覺化設定面板，無需記憶複雜指令：

### 1. 互動設定面板
<p align="center">
  <img src="assets/setup_control_panel.svg" alt="PTT-DC-Bot /setup 視覺化互動設定面板" width="680"/>
</p>

### 2. 熱門看板下拉勾選
<p align="center">
  <img src="assets/dropdown_boards_select.svg" alt="快速選擇監控看板下拉選單" width="680"/>
</p>

### 3. 新增追蹤關鍵字（支援預算上限與排除詞）
<p align="center">
  <img src="assets/keyword_modal_form.svg" alt="新增追蹤關鍵字彈出表單" width="560"/>
</p>

### 4. 即時推播通知卡片預覽
<p align="center">
  <img src="assets/notification_embed_preview.svg" alt="即時推播通知 Embed 卡片預覽" width="680"/>
</p>

---

## 權限與重要說明

- **Discord 特權意圖**：**不需要啟用 Privileged Gateway Intents，也不需要開啟 Message Content Intent。**  
  Discord 原生即對機器人的私訊 (DM)、明確 `@Bot` 提及與 Slash 指令提供完整內容。
- **邀請權限整數**：`274877975552`（包含 View Channels, Send Messages, Embed Links, Read Message History），Scopes 勾選 `bot` 與 `applications.commands`。

---

## 常用指令速查

| 指令 | 說明 |
| :--- | :--- |
| `/setup` | 開啟視覺化互動設定面板（推薦首次使用） |
| `/ai` | 用自然語言請 AI 助理設定規則、查詢看板或回溯現貨 |
| `/keyword_add` | 新增精確關鍵字（支援空格 AND、`-` 排除詞與預算上限） |
| `/fuzzy_add` | 新增 AI 模糊語意追蹤規則（依賴語意判讀保固、盒裝、地點） |
| `/keyword_list` | 檢視當前已啟用的追蹤規則清單 |
| `/scan_unsold` | 回溯掃描版上近期尚未售出且未洽談的現貨商品 |
| `/status` | 查看機器人巡檢狀態、健康度與 Groq 多模型佇列負載 |
| `/pause` / `/resume` | 暫停或恢復推播通知 |
| `/forget_me` | 徹底永久刪除個人的所有規則、推播紀錄與對話記憶 |

---

## 本地測試與驗證

```bash
/tmp/ptt_venv/bin/pytest -q
/tmp/ptt_venv/bin/python tools/eval_sold.py
```

## 授權

本專案採 [MIT License](LICENSE) 授權釋出。
