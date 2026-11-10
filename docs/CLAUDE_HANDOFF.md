# PTT-DC-Bot 工作交接與審查維護手冊 (Claude Code & Developer Handoff)

> 本手冊同步自專案根目錄的 [CLAUDE.md](../CLAUDE.md)，專供 **Claude Code** 及協同維護者進行代碼審查與系統維護。

---

## 快速導覽目錄

1. [專案概況與最新狀態](../CLAUDE.md#1-專案概況與最新狀態-project-overview--status)
2. [登入權限與環境變數憑證配置](../CLAUDE.md#2-登入權限與環境變數憑證配置-credentials--permissions)
   - Discord（免特權意圖、OAuth2 權限說明）
   - Telegram（長輪詢架構、Token 與 Chat ID）
   - Email SMTP（Gmail 應用程式密碼、防標頭注入）
   - Groq API（三模型輪替池、無金鑰純規則運作模式）
3. [系統整體架構與核心模組職責](../CLAUDE.md#3-系統整體架構與核心模組職責-architecture--modules)
   - 純函式售出狀態機 (`sold_rules.py`)
   - 多品項獨立虛擬視角切分 (`apply_keyword_view`)
   - 18 小時原位劃線更新機制 (`check_sold_deliveries_once`)
   - Crawl-Once, Fan-Out 高效全域共享快取
   - Prompt Injection 縱深防禦 (`wrap_untrusted` & Intent Guard)
4. [常用維運與測試指令](../CLAUDE.md#4-常用維運與測試指令-operational--test-commands)
   - Pytest 全套驗證 (`/tmp/ptt_venv/bin/pytest -q`)
   - 80 筆真實樣本售出評測 (`tools/eval_sold.py`)
5. [開發與審查規範](../CLAUDE.md#5-開發與審查規範-development--review-conventions)

請直接參閱專案根目錄之完整交接文檔：[CLAUDE.md](../CLAUDE.md)。
