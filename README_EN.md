# PTT-DC-Bot

**English** | [繁體中文](README.md)

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![Groq API](https://img.shields.io/badge/LLM-Groq%20API-f55036)](https://groq.com/)
[![Docker Image](https://img.shields.io/badge/Docker-GHCR%20Multi--Arch-2496ED?logo=docker&logoColor=white)](https://github.com/jasonxp516/ptt-dc-bot/pkgs/container/ptt-dc-bot)
[![SQLite WAL](https://img.shields.io/badge/Database-SQLite%20WAL-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/wal.html)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An asynchronous PTT (Taiwan Bulletin Board System) board monitor and Discord push notification bot combining deterministic rule-based filtering with Groq LLM semantic reasoning.

Built specifically for high-velocity second-hand trading boards (`HardwareSale`, `MacShop`, `nb-shopping`, `Gamesale`, `MobileComm`, etc.), PTT-DC-Bot resolves the common pain points of traditional alert bots: dead notifications for already-sold items, missed products in multi-item listings, and hardware model specifications being misidentified as asking prices.

---

## Why PTT-DC-Bot? (Key Differences from Existing Tools)

Most existing PTT alert solutions (such as PttAlertor, RSS bots, or basic webhook scripts) rely strictly on simple title keyword matching and one-way notification pushing. When monitoring fast-moving marketplace boards, several critical issues arise. PTT-DC-Bot solves them by design:

1. **18-Hour In-Place Status Updates (Zero Channel Spam)**  
   Traditional bots abandon a post once pushed, leaving notification channels cluttered with stale, sold-out listings. PTT-DC-Bot actively monitors delivered posts for up to 18 hours. It tracks both title edits and **the original author's latest post-signature comments** (e.g., "sold", "reserved", "pending", "no more emails"). Upon detecting a completed deal, it **edits the original Discord embed in-place with a strikethrough** without sending repetitive messages.
2. **Isolated Multi-Item Post Slicing**  
   When a seller lists multiple products in a single post (e.g., `1. GTX 1060 (Sold) / 2. RTX 3060Ti $5800`), standard scrapers discard the entire post upon finding the word "Sold". PTT-DC-Bot creates independent virtual views for each keyword, accurately tracking price and availability for each item separately.
3. **Hardware Model vs. Price Disambiguation**  
   Built-in masking filters prevent hardware specifications like `RTX 3060`, `12G`, `750W`, or `i5-12400` from being parsed as prices. The engine also normalizes shorthand price notations (such as `5.8k` → 5,800 TWD) for reliable budget filtering.
4. **Natural Language Setup & Zero-Cost Semantic Verification**  
   Integrated with Groq's free-tier LLM API, users can define tracking rules in plain natural language (e.g., *"Track RTX 3060Ti under 6,500 TWD with original warranty in Taipei"*). A dynamic rotation pool across 3 free models (~24,000 TPM combined) and a P0–P3 priority queue ensures real-time background inspection without triggering rate limits.
5. **Unified Multi-Tenant Architecture (Server Channels + Personal DMs)**  
   Powered by SQLite in WAL mode, a single bot instance simultaneously powers public server channels and private direct messages. Individual members can DM the bot to maintain independent watchlists and quotas completely isolated from the server.

<details>
<summary><b>Click to expand: Feature Comparison Matrix (vs. PttAlertor / Webhook Scripts / RSS Bots)</b></summary>
<br>

| Evaluation Criteria | Basic Webhook Scripts | PttAlertor | RSS Notification Bots | **PTT-DC-Bot (This Project)** |
| :--- | :--- | :--- | :--- | :--- |
| **Interaction Model** | One-way push (config file edits) | LINE / Telegram text commands | Slash commands (URL only) | **Native Slash commands + Interactive `/setup` UI + Natural language DM chat** |
| **Multi-Tenancy** | None (single static configuration) | Per user account | Per channel | **Shared guild channel broadcasts + isolated personal DM watchlists** |
| **Sold Detection** | Title keyword only | Title keyword or none | None | **Title regex + post-signature author comments + LLM semantic review** |
| **Stale Post Handling** | None | None | None | **In-place Discord embed edit with strikethrough (18-hour tracking window)** |
| **Multi-Item Posts** | Whole-post judgment | Whole-post judgment | None | **Per-keyword virtual view slicing for independent status & price parsing** |
| **Budget Filtering** | Basic regex (prone to model false-hits) | None | None | **Hardware model shielding, `k`-unit parsing, and max price limits** |
| **Historical Stock Scan** | None | None | None | **`/scan_unsold` command to scan recent posts for available items** |
| **Crawler Efficiency** | Redundant fetches per subscription | Centralized polling | Dependent on RSS feed cache | **Crawl-Once, Fan-Out with shared article content and AI judgment caches** |

</details>

---

## Quick Start & Deployment: 3 Streamlined Options

Before running the bot, please prepare two free credentials:
1. **Discord Bot Token**: Open the [Discord Developer Portal](https://discord.com/developers/applications), create an Application, go to the **Bot** tab, and click **Reset Token**; **be sure to enable `Message Content Intent` below**.
2. **Groq API Key (Free)**: Sign up or log in at [Groq Console](https://console.groq.com/keys) and click **Create API Key** (starts with `gsk_...`).

> **💡 Experience Upgrade**: If you use **Method 1** or **Method 2**, you **do not need to create or edit a `.env` file manually**! The built-in setup wizard will guide you through terminal inputs, validate tokens in real time, and **automatically calculate your bot's dedicated Discord OAuth2 invite link with full permissions**, ready to add to your server with one click.

---

### Method 1: One-Liner Automatic Installation (Fastest Setup)

Open your terminal (Terminal on Linux/macOS or PowerShell on Windows) and paste the command for your operating system. The script automatically handles downloading, virtualenv provisioning, dependency installation, setup wizard execution, invite link generation, and service startup:

#### Linux / macOS
```bash
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.sh | bash
```

#### Windows (PowerShell)
```powershell
irm https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.ps1 | iex
```

*Key Highlights*:
- Automatically detects Python 3.10+ (on Windows, prompts to auto-install via `winget` if missing).
- Automatically provisions an isolated virtual environment (`.venv`), keeping your global Python clean.
- Automatically launches the interactive setup wizard—simply paste your Token and Key when prompted.
- Provides immediate startup options (Linux supports starting in a background `tmux` session).

---

### Method 2: GitHub Releases Double-Click Bundles (Zero-Command GUI)

Ideal for users who prefer graphical double-click launchers without running terminal commands:

1. Visit the project's [GitHub Releases page](https://github.com/jasonxp516/ptt-dc-bot/releases) and download the pre-packaged archive for your platform:
   - **Windows**: Download `ptt-dc-bot-<version>-windows.zip`
   - **macOS**: Download `ptt-dc-bot-<version>-macos.tar.gz`
2. Extract the archive into any folder on your computer (e.g., Desktop or personal documents).
3. Run the launcher:
   - **On Windows**: Double-click **`start_windows.bat`**
   - **On macOS**: Double-click **`start_mac.command`**
4. On first launch, the environment will initialize automatically and launch the configuration wizard. Paste your credentials when prompted, and the bot will start immediately.

*Watchdog Recovery*:
- The Windows launcher includes an automatic crash-recovery watchdog: if the bot encounters a network glitch or abnormal termination, it will automatically restart after 10 seconds. Closing the window or cancelling setup exits cleanly.

---

### Method 3: Docker Compose & GHCR Pre-Built Images

Ideal for Linux servers, Raspberry Pi, home labs, or NAS (Synology / unRAID / QNAP) container deployments. Pre-built multi-architecture images (`linux/amd64` and `linux/arm64`) are published to: `ghcr.io/jasonxp516/ptt-dc-bot:latest`.

#### 1. Standard Docker Compose Deployment

Create a working directory and configuration files:

```bash
mkdir -p ptt-dc-bot/data && cd ptt-dc-bot

# Download official docker-compose.yml and configuration template
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/docker-compose.yml -o docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/.env.example -o .env

# Set secure permissions and edit credentials
chmod 600 .env
nano .env
```

Fill in your credentials in `.env`:
```ini
DISCORD_TOKEN=your_discord_bot_token_here
GROQ_API_KEY=your_groq_api_key_here
```

Start the container:
```bash
# Pull the latest pre-built GHCR image and start in background
docker compose pull && docker compose up -d

# View real-time logs (startup confirms with "Logged in as ...")
docker compose logs -f

# Stop service
docker compose down
```

`docker-compose.yml` comes pre-configured with principle of least privilege (`cap_drop: ALL`, `no-new-privileges: true`), resource limits (1.0 CPU, 512MB RAM), and database volume persistence (`./data:/app/data`).

---

#### 2. Synology NAS (Container Manager) Deployment

1. **Prepare Directory & Config**:
   - Open DSM **File Station** and create the directory structure under the `docker` shared folder:
     ```text
     /docker/ptt-dc-bot/
     ├── .env           (Text file containing DISCORD_TOKEN and GROQ_API_KEY)
     └── data/          (Empty folder for SQLite database and log persistence)
     ```
2. **Create Project in Container Manager**:
   - Open **Container Manager**, switch to the **Project** tab.
   - Click **Create**:
     - **Project Name**: `ptt-dc-bot`
     - **Path**: Select the `/docker/ptt-dc-bot` directory created above.
     - **Source**: Select "Create docker-compose.yaml" and paste the contents of `docker-compose.yml`.
   - Click Next. Container Manager will download `ghcr.io/jasonxp516/ptt-dc-bot:latest` and start the container.
3. **Verify Operation**:
   - Click container **Details** → **Log**, and confirm output includes `Logged in as ...`.

---

#### 3. unRAID Container Deployment

1. Open the unRAID Web GUI, go to the **Docker** tab, scroll to the bottom and click **Add Container**.
2. Configure container parameters:
   - **Name**: `ptt-dc-bot`
   - **Repository**: `ghcr.io/jasonxp516/ptt-dc-bot:latest`
   - **Restart Policy**: `unless-stopped`
3. Click **Add another Path, Port, Variable, or Device** to add:
   - **Path**:
     - Container Path: `/app/data`
     - Host Path: `/mnt/user/appdata/ptt-dc-bot`
     - Access Mode: `Read/Write`
   - **Variables**:
     - Name: `DISCORD_TOKEN`, Value: `your_discord_bot_token_here`
     - Name: `GROQ_API_KEY`, Value: `your_groq_api_key_here`
     - Name: `TZ`, Value: `Asia/Taipei`
4. Click **Apply**. The container will be created and run continuously in the background.

---

## Discord Onboarding & Visual `/setup` Control Panel

Once your bot is online, channel configuration, board subscriptions, and keyword management can all be completed directly within Discord via interactive buttons and menus, without needing to memorize complex command syntax.

### 1. Adding the Bot to Your Server (OAuth2 Permissions)

- **Auto-Generated Invite Link**:  
  When using the installation script or double-click launcher, the setup wizard validates your Token and automatically prints your dedicated OAuth2 invite URL with permissions integer `274877975552`. Simply open the link in your browser and select your server.
- **Manual Invite Verification**:  
  If creating an invite URL manually, ensure it includes Scopes `bot` and `applications.commands`, with the following Bot Permissions:
  - `View Channels`
  - `Send Messages`
  - `Embed Links` (required for notification cards)
  - `Read Message History`

> **⚠️ Critical Note**: Make sure to visit the [Discord Developer Portal](https://discord.com/developers/applications) → Your Bot → **Bot** tab → **Privileged Gateway Intents**, enable **Message Content Intent**, and save changes. Without this intent, the bot cannot process conversational commands or direct messages.

---

### 2. First-Boot Owner Welcome DM (Clean Boot)

When the bot connects to Discord for the first time with a clean database (no bound channels and no keywords configured):
1. The terminal prints a prominent startup guide notice:
   ```text
   ========================================================================
   [PTT-DC-Bot] 首次啟動導引：
   機器人已成功登入！請在 Discord 伺服器或私訊中輸入 /setup 快速完成推播與看板設定。
   ========================================================================
   ```
2. The bot automatically sends a welcome onboarding DM to the Application Owner (the bot's creator), providing 4 quick start steps and a summary of core commands.

---

### 3. The `/setup` Visual Dashboard

Type `/setup` in any server text channel (or inside a private DM with the bot) to open the visual control panel:

```text
+--------------------------------------------------------------+
| ⚙️ PTT-DC-Bot 視覺化互動設定面板                              |
| 歸屬範圍：🏠 伺服器共用追蹤                                   |
| 目前推播目標：⚠️ 尚未綁定                                    |
| 系統監控看板：HardwareSale                                    |
| 已設定關鍵字：尚未設定關鍵字                                 |
+--------------------------------------------------------------+
| [📋 快速選擇監控看板（可多選）...                         ▼] |
| [📌 綁定此頻道為推播頻道] [➕ 新增追蹤關鍵字] [🔔 測試推播通知] |
+--------------------------------------------------------------+
```

Four-step walkthrough:

1. **Step 1: Click "📌 綁定此頻道為推播頻道" (Bind Current Channel)**  
   - Binds the current text channel as the notification target for the server with one click.
   - If clicked inside a private DM with the bot, sets the destination to your personal DM.
2. **Step 2: Select Boards in "📋 快速選擇監控看板" (PopularBoardsSelect)**  
   - The dropdown menu is pre-populated with top trading boards:
     - `HardwareSale`: PC hardware (GPUs, CPUs, RAM, power supplies, peripherals) 🖥️
     - `MacShop`: Apple marketplace (MacBooks, iPhones, iPads, Apple accessories) 🍎
     - `nb-shopping`: Laptops & notebooks (Gaming laptops, ultrabooks) 💻
     - `Gamesale`: Video games (Consoles, PS5/Switch game cartridges) 🎮
     - `MobileComm`: Mobile communications (Smartphones, cellular hardware) 📱
   - Supports single or multiple selections. Selecting boards establishes initial baselines immediately without spamming historical posts.
3. **Step 3: Click "➕ 新增追蹤關鍵字" (Add Keyword Modal Form)**  
   - Opens a pop-up modal dialog with 4 structured fields:
     - **Keyword (Required)**: Supports space AND logic (e.g., `3060Ti Taipei`) and minus exclusion (e.g., `3060 -laptop -mining`).
     - **Board (Optional)**: Defaults to `HardwareSale`.
     - **Category (Optional)**: Defaults to `賣` (Selling, excluding buying/WTS requests).
     - **Max Price (Optional)**: Numeric budget ceiling (e.g., `6500`). Posts priced higher than this amount are filtered out automatically.
4. **Step 4: Click "🔔 測試推播通知" (Test Push Notification)**  
   - Dispatches a fully formatted test notification embed to the bound channel/DM immediately.
   - Confirms that the bot possesses send and embed permissions and verifies card rendering.

---

## Daily Usage Guide

PTT-DC-Bot supports both **Personal DMs** and **Server Channels**:
- **Personal DM Tracking**: Direct-message the bot or run slash commands in private chat. Push alerts are delivered straight to your DMs without needing server permissions.
- **Server Channel Alerts**: An administrator binds a channel using `/setup` or `/channel_set`. All rules configured for the server push notifications to that designated channel.

### 1. Conversational AI Assistant (DM Chat or `/ai`)

Talk to the bot in plain conversational English or Traditional Chinese; the ReAct AI agent automatically identifies intent and calls underlying tools:
- *"Track RTX 4070 Super on HardwareSale with a budget under 18,000 TWD"*
- *"Find an RTX 3060Ti with active original warranty and Taipei in-person trade under 6,500"*
- *"Scan HardwareSale for currently unsold 6700XT listings"*
- *"Show all my active tracking rules"*

### 2. Exact Keyword Rules (`/keyword_add`)
High-speed deterministic matching that does not consume daily AI chat quotas:
- **Space represents AND**: `Taipei 3060TI` (matches posts containing both "Taipei" and "3060TI").
- **Minus `-` represents NOT**: `3060 -ti -laptop -mining` (matches 3060, strictly excluding 3060Ti, laptops, and mining cards).
- **Budget Cap (`max_price`)**: e.g., `6000` (automatically ignores posts priced above 6,000 TWD).

### 3. AI Fuzzy Semantic Rules (`/fuzzy_add`)
Designed for criteria that cannot be determined by title keywords alone (e.g., warranty duration, packaging completeness, specific trade locations):
- `description`: Describe your tracking condition (e.g., `RTX 3060Ti with original box and warranty, non-mining`).
- `hint_keywords` (*recommended*): Fast pre-filtering terms (e.g., `3060,3060ti`). The LLM is only invoked when post titles match the pre-filter, optimizing processing speed.

### 4. Scan Currently Available Stock (`/scan_unsold`)
Don't want to wait for new posts? Run `/scan_unsold keyword: 3060TI`. The engine back-scans recent posts on the board, filters out sold or closed items via signature comments and LLM review, and renders a paginated catalog of available items with navigation buttons.

---

## Advanced Configuration & System Management (Click to expand)

Full command references, background daemon services, environment variables, and system internals are organized below:

<details>
<summary><b>1. Complete 24 Slash Commands Reference</b></summary>
<br>

#### Visual Setup & AI Search
| Command | Parameters | Description |
| :--- | :--- | :--- |
| `/setup` | `scope` | Opens the interactive visual UI control panel (channel bind, popular boards, keyword modal, test push) |
| `/ai` | `prompt`, `scope` | Converse with the ReAct AI agent to create rules, discover boards, or scan stock |
| `/scan_unsold` | `keyword`, `board`, `max_per_keyword` | Back-scans recent board posts and compiles an unsold inventory list (with pagination buttons) |
| `/search` | `keyword`, `board`, `pages` | Native PTT search across historical posts (does not filter sold status) |

#### Exact Keyword Management
| Command | Parameters | Description |
| :--- | :--- | :--- |
| `/keyword_add` | `keyword`, `board`, `category`, `max_price` | Adds an exact keyword rule (supports space AND, `-` exclusion, price cap) |
| `/keyword_edit` | `keyword_id`, `new_keyword`, `max_price` | Edits an existing keyword or budget ceiling (`0` removes price cap) |
| `/keyword_remove` | `keyword`, `scope` | Removes a specific keyword rule (with autocomplete support) |
| `/keyword_list` | `scope` | Lists all active keywords, fuzzy rules, and notification targets |
| `/keyword_clear` | `scope` | Clears all exact keyword rules in the specified scope |

#### AI Semantic Rules & Board Management
| Command | Parameters | Description |
| :--- | :--- | :--- |
| `/fuzzy_add` | `description`, `board`, `category`, `hint_keywords` | Adds a natural language AI semantic monitoring rule |
| `/fuzzy_remove` | `rule`, `scope` | Removes an active AI fuzzy rule |
| `/fuzzy_list` | `scope` | Lists active AI fuzzy rules in the current scope |
| `/board_add` | `board`, `scope` | Validates and adds a PTT board to the polling schedule (baseline sync prevents old post spam) |
| `/board_remove` | `board`, `scope` | Removes a monitored board and its associated rules |
| `/board_list` | None | Displays all PTT boards currently polled by the system |

#### Channel Setup, Account & Administration
| Command | Parameters | Description |
| :--- | :--- | :--- |
| `/channel_set` | `channel`, `scope` | Binds notification target (running in DMs without arguments binds DM delivery) |
| `/pause` / `/resume` | `scope` | Pauses or resumes push alerts while preserving all configured rules |
| `/me` | None | Displays your personal rules, DM connection status, and remaining daily quotas |
| `/forget_me` | None | Permanently erases your personal data, rules, delivery history, and conversation memory |
| `/status` | `scope` | Displays bot uptime, Groq queue health, and crawler performance metrics |
| `/check_now` | None | Triggers an immediate crawler polling cycle (5-minute cooldown for non-admins) |
| `/push_mode` | `mode` (`keyword` / `all`) | Toggles between keyword matching and all-post forwarding (admin only) |
| `/admin` | `action`, `target_id`, `tier` | System owner panel: inspect global stats, manage subscriber tiers, or update blacklists |
| `/help` | None | Displays command summary and usage guide |

</details>

<details>
<summary><b>2. Background Daemons (Linux systemd & tmux)</b></summary>
<br>

If you prefer running natively on a Linux VPS or dedicated host with standard Python, two daemon options are provided:

#### (A) Managing with Linux `systemd` (Recommended for Production)
The repository includes a ready-to-use `ptt-bot.service` unit template:
```bash
# 1. Copy service template to system directory
sudo cp ptt-bot.service /etc/systemd/system/ptt-bot.service

# 2. Edit service file to update User and WorkingDirectory to your actual paths
sudo nano /etc/systemd/system/ptt-bot.service

# 3. Reload systemd daemon and enable service on boot
sudo systemctl daemon-reload
sudo systemctl enable --now ptt-bot

# 4. Check service status and real-time logs
sudo systemctl status ptt-bot
journalctl -u ptt-bot -f
```

#### (B) Managing with `tmux` Daemon Scripts
If `tmux` is available on your host, use the built-in management scripts (includes automatic 10-second crash recovery):
```bash
# Start background session (Session name: ptt_bot)
bash start_bg.sh

# View real-time log output
tail -f bot.log

# Stop service
bash stop_bg.sh

# Restart service
bash restart_bg.sh
```

</details>

<details>
<summary><b>3. Complete .env Configuration Reference</b></summary>
<br>

| Variable Name | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `DISCORD_TOKEN` | **Yes** | None | Discord Bot authentication token |
| `GROQ_API_KEY` | **Yes** | None | Groq Cloud API key (powers natural language assistant & sold semantic detection) |
| `DEFAULT_CHANNEL_ID` | No | Empty | Default Discord notification channel ID (recommended to configure via `/setup`) |
| `DEFAULT_GUILD_ID` | No | Empty | Primary Discord server ID (assigned owner-tier quotas) |
| `OWNER_USER_ID` | No | Empty | Bot administrator Discord User ID (auto-resolved from guild owner if omitted) |
| `DEFAULT_BOARD` | No | `HardwareSale` | Default PTT board to monitor upon initial startup |
| `POLL_INTERVAL_MIN` | No | `90` | Minimum background polling interval in seconds |
| `POLL_INTERVAL_MAX` | No | `150` | Maximum background polling interval in seconds (jittered to avoid fixed patterns) |
| `GROQ_MODEL` | No | `openai/gpt-oss-120b` | Primary high-parameter LLM model for semantic reasoning |
| `GROQ_FAST_MODEL` | No | `openai/gpt-oss-20b` | Lightweight LLM model for fast preliminary screening |
| `DB_PATH` | No | `bot_data.db` | SQLite database file path (`/app/data/bot_data.db` in Docker) |
| `LOG_PATH` | No | `bot.log` | Log file path (5MB rotating log with 3 retained backups) |

</details>

<details>
<summary><b>4. System Architecture & Directory Layout</b></summary>
<br>

```text
ptt-dc-bot/
├── bot.py               # Discord Client, 24 Slash commands, /setup UI View, polling engine
├── setup_wizard.py      # Standalone interactive CLI configuration wizard (standard library only)
├── install.sh           # Linux / macOS one-liner installation script
├── install.ps1          # Windows PowerShell one-liner installer
├── start_windows.bat    # Windows double-click launcher with crash recovery loop
├── start_mac.command    # macOS double-click launcher
├── config.py            # Environment validation, quota constants, and input sanitization
├── storage.py           # SQLite WAL multi-tenant database, schema migrations, atomic backups
├── ptt_crawler.py       # Async PTT crawler, dual-layer sold detection, per-item virtual views
├── groq_scheduler.py    # Priority queue (P0~P3) and dynamic 3-model rate-limit scheduler
├── groq_agent.py        # ReAct AI agent, tool execution guards, and prompt injection defense
├── delivery.py          # Notification dispatcher, 3-day deduplication, 18-hour in-place edits
├── embeds.py            # Discord Embed formatting, character limit truncation, pagination
├── Dockerfile           # Python 3.11-slim container image definition (non-root execution)
├── docker-compose.yml   # Multi-arch container orchestration and ./data volume persistence
├── .dockerignore        # 26 exclusion rules preventing credentials, logs, and cache packaging
├── .github/workflows/   # release.yml CI/CD test gate, release packages, and GHCR publishing
└── tests/               # 100% offline mock unit test suite
```

**Crawl-Once, Fan-Out Polling Pipeline**:
1. **Active Board Aggregation**: Only boards with active subscriber rules are queried; unmonitored boards are skipped.
2. **Shared Fetch & Caching**: Each active board is fetched once per cycle. Article text caches (10-minute TTL) and AI judgment caches (60-minute TTL) are shared across all subscribers, preventing PTT 503 rate-limiting.
3. **Per-Keyword Virtual Slicing**: Slices multi-item posts via `apply_keyword_view` to decouple item prices and sold statuses.
4. **18-Hour Round-Robin In-Place Tracking**: Delivers updates via in-place message editing when items are confirmed sold.

</details>

<details>
<summary><b>5. Groq Multi-Model Scheduler & Quota Tiering</b></summary>
<br>

Groq's free tier imposes per-minute token limits (TPM) and daily request limits (RPD) per model. To prevent background crawling and interactive user queries from interfering with each other, `groq_scheduler.py` implements dedicated scheduling:

- **3-Model Dynamic Rotation Pool**: Rotates requests across `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, and `openai/gpt-oss-20b`, expanding free capacity to ~24,000 TPM.
- **4-Tier Priority Queue**:
  - `P0 (Interactive)`: User DMs and `/ai` commands (top priority).
  - `P1 (Realtime)`: Semantic verification for newly crawled articles.
  - `P2 (Batch Scan)`: `/scan_unsold` operations (yields capacity to P0/P1 when quota drops below 30%).
  - `P3 (Maintenance)`: Low-priority background cleanup.

**Default Subscriber Quota Table** (background pushes do not consume daily conversation quotas):

| Quota Metric | Personal DM (`friend`) | Standard Server (`free`) | Admin / Primary Server (`owner`) |
| :--- | :---: | :---: | :---: |
| Exact Keywords | 30 | 10 | 999 |
| AI Fuzzy Rules | 8 | 3 | 999 |
| Monitored Boards | 5 | 5 | 20 |
| Daily `/ai` & DM Chats | 60 / day | 20 / day | Unlimited |
| Daily `/scan_unsold` Scans | 25 / day | 8 / day | Unlimited |

</details>

<details>
<summary><b>6. Security Hardening Design (Prompt Injection / RBAC / SSRF / Container Hardening)</b></summary>
<br>

To guard against malicious inputs and unauthorized access in public Discord servers, the system implements defense-in-depth:
- **Prompt Injection Defense**: Scraped external content is XML-escaped via `escape_prompt_xml` and quarantined within `<untrusted_ptt_content>` tags. The execution layer enforces **Intent Guards**: external content cannot trick the AI into mutating or deleting subscriber rules.
- **Multi-Tenant RBAC Isolation**: Standard guild members cannot alter server-wide rules. All database updates require an explicit `subscriber_id`.
- **SSRF & Parameterized Queries**: The crawler strictly enforces `https://www.ptt.cc/bbs/` URL domains with `allow_redirects=False`. All SQLite queries employ parameterized placeholders and `LIKE ... ESCAPE '/'`.
- **Error Desensitization & Non-Root Containers**: Frontend error responses mask local system paths and stack traces. Docker containers execute under UID 1000 (`botuser`) with read-only application files and stripped privileges (`cap_drop: [ALL]`).

</details>

<details>
<summary><b>7. Frequently Asked Questions & Troubleshooting (FAQ)</b></summary>
<br>

**Q1: I configured rules in DM, but why am I not receiving notification pushes?**  
Run `/me` to inspect your account status. If it indicates a previous DM delivery failure, your Discord privacy settings are blocking messages from the bot. Go to Discord **User Settings → Privacy & Safety**, enable **"Allow direct messages from server members"** for the mutual server, and send any direct message to the bot to automatically restore alerts.

**Q2: Why do I receive an "Insufficient Permissions" error when running commands in a server channel?**  
Executing configuration commands in a server channel defaults to modifying **shared server rules**, which requires server administrator or channel management permissions. If you only want to configure your personal watchlist, either **DM the bot directly** or specify the `scope` parameter as `Personal (user)`.

**Q3: Why was an article matching my keyword not pushed?**  
The system automatically filters out posts matching any of these conditions:
1. Post is older than 21 days.
2. Title or author's signature comments indicate the item is sold, reserved, or closed.
3. The same seller reposted the identical item within the last 3 days.
4. The post's extracted price exceeds your configured `max_price`.  
*(To inspect all posts including sold ones, use `/search`).*

</details>

---

## Running Offline Tests

The project includes a comprehensive offline unit and integration test suite using ephemeral SQLite databases and mocks (requires zero internet access and zero real credentials):

```bash
# Run full offline test suite
LOG_PATH=/tmp/test.log DB_PATH=/tmp/test.db PYTHONPYCACHEPREFIX=/tmp/pycache PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover tests
```

Testing covers setup wizard CLI, token offline validation, OAuth2 URL calculation, cross-platform launcher syntax, Discord `/setup` interactive UI, CI/CD release workflows, and security guardrails.
