# PTT-DC-Bot

**English** | [繁體中文](README.md)

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![Groq API](https://img.shields.io/badge/LLM-Groq%20API-f55036)](https://groq.com/)
[![SQLite WAL](https://img.shields.io/badge/Database-SQLite%20WAL-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/wal.html)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An asynchronous PTT board monitor and Discord notification bot combining rule-based filtering with Groq LLM semantic verification.
Built primarily for second-hand marketplace boards on PTT (`HardwareSale`, `MacShop`, `nb-shopping`, `Gamesale`, etc.) to solve common pain points in existing alert tools: dead links to already-sold items, false negatives on multi-item posts, and hardware model numbers being mistaken for prices.

---

## Why This Project? (How It Differs from Existing Tools)

Most existing PTT alert tools (such as PttAlertor, RSS bots, or basic webhook scripts) only perform title keyword matching and one-way message pushing. When used on fast-moving second-hand trading boards, several practical issues arise. This bot addresses them directly:

1. **In-place Sold Status Updates**
   Traditional bots stop tracking a post once the alert is sent, leaving channels full of items that were sold hours ago. This bot monitors pushed posts for 18 hours—checking both title edits and **the author's latest comments beneath the signature block** (e.g., "sold", "pending", "stop PMing")—and **edits the original Discord embed in-place with a strikethrough** without sending extra spam messages.
2. **Per-Item Parsing for Multi-Item Posts**
   When a single post lists multiple items (e.g., `1. GTX 1060 (Sold) / 2. RTX 3060Ti $5800`), naive scrapers skip the entire post upon seeing the word "Sold". This bot slices a virtual view per keyword, tracking availability and price independently for each item.
3. **Hardware Model vs. Price Disambiguation**
   Built-in model shielding prevents strings like `RTX 3060`, `12G`, `750W`, or `i5-12400` from being extracted as the item price. It also normalizes shorthand like `5.8k` into `5800` for accurate budget filtering.
4. **Natural Language Setup & Semantic Filtering (Zero API Cost)**
   Integrates with Groq's free API tier using a 3-model rotation pool (~24,000 TPM combined) and a P0–P3 priority queue. Users can configure tracking rules or filter by complex conditions (e.g., "under 6500 TWD, under warranty, not mined") in plain language without hitting rate limits.
5. **Multi-Tenant Architecture (Server Channels + Personal DMs)**
   Backed by SQLite in WAL mode, a single bot instance can serve shared server notification channels while allowing individual users to DM the bot and manage their own private watchlists with isolated rules and quotas.

<details>
<summary><b>Click to expand: Feature Comparison (vs. PttAlertor / Webhook Scripts / RSS Bots)</b></summary>
<br>

| Feature | Basic Webhook Scripts | PttAlertor | RSS Bots | **PTT-DC-Bot (This Project)** |
| :--- | :--- | :--- | :--- | :--- |
| **Interaction** | One-way push (config files) | LINE / TG text commands | Slash commands (URL only) | **Native Slash commands + DM natural language + pagination buttons** |
| **Multi-tenancy** | None (single config) | Per user account | Per channel | **Shared guild channels + isolated personal DM watchlists** |
| **Sold Detection** | Title keyword only | Title keyword or none | None | **Title regex + post-signature author comments + LLM semantic check** |
| **Stale Post Handling** | None | None | None | **In-place Discord embed edit with strikethrough (18h window)** |
| **Multi-item Posts** | Whole-post judgment | Whole-post judgment | None | **Per-keyword item slicing for independent status & price** |
| **Budget Filtering** | Basic regex (prone to model # errors) | None | None | **Hardware model masking, `k`-unit conversion, max-price filter** |
| **Historical Stock Scan** | None | None | None | **`/scan_unsold` command to scan recent posts for available items** |
| **Crawler Efficiency** | Redundant fetches per rule | Centralized fetch | Depends on RSS feed delay | **Crawl-Once, Fan-Out with shared content & AI caches** |

</details>

---

## Quick Start & Deployment

### 1. Prerequisites

You need two free credentials before starting the bot:

#### (1) Create a Discord Bot & Get the Token
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. Open the **Bot** tab on the left:
   - Click **Reset Token** to obtain your `DISCORD_TOKEN`.
   - Scroll down to **Privileged Gateway Intents**, enable **Message Content Intent** (required for DMs and natural language interaction), and save changes.
3. Open **OAuth2 -> URL Generator**:
   - Under **Scopes**, check `bot` and `applications.commands`.
   - Under **Bot Permissions**, check `Send Messages`, `Embed Links`, `Read Message History`, and `View Channels`.
   - Copy the generated URL at the bottom and open it in your browser to invite the bot to your server.

#### (2) Get a Free Groq API Key
1. Sign in at [Groq Console](https://console.groq.com/keys).
2. Click **Create API Key** and copy the key (`gsk_...`).

---

### 2. Configure Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` and fill in your two required credentials:

```ini
# Required: Discord Bot Token & Groq API Key
DISCORD_TOKEN=your_discord_bot_token_here
GROQ_API_KEY=your_groq_api_key_here
```

> **Note**: `DEFAULT_CHANNEL_ID` and `DEFAULT_GUILD_ID` in `.env` are optional. Once the bot is running, you can simply run `/channel_set` inside any Discord channel to bind notifications without looking up IDs manually.

<details>
<summary><b>Click to expand: Full .env Configuration Reference</b></summary>
<br>

| Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `DISCORD_TOKEN` | Yes | None | Discord Bot authentication token |
| `GROQ_API_KEY` | Yes | None | Groq Cloud API key (used for natural language agent & semantic checks) |
| `DEFAULT_CHANNEL_ID` | No | Empty | Default Discord notification channel ID (can be set via `/channel_set`) |
| `DEFAULT_GUILD_ID` | No | Empty | Primary Discord server ID (granted owner-tier quotas) |
| `OWNER_USER_ID` | No | Empty | Bot owner Discord User ID (auto-detected from primary guild owner if empty) |
| `DEFAULT_BOARD` | No | `HardwareSale` | Default PTT board to monitor |
| `POLL_INTERVAL_MIN` | No | `90` | Minimum background polling interval in seconds |
| `POLL_INTERVAL_MAX` | No | `150` | Maximum background polling interval in seconds (jittered) |
| `GROQ_MODEL` | No | `openai/gpt-oss-120b` | Primary LLM model for semantic reasoning |
| `GROQ_FAST_MODEL` | No | `openai/gpt-oss-20b` | Fast lightweight LLM model |
| `DB_PATH` | No | `bot_data.db` | SQLite database file path (`/app/data/bot_data.db` in Docker) |
| `LOG_PATH` | No | `bot.log` | Rotating log file path (5MB max, 3 backups) |

</details>

---

### 3. Start the Bot (Choose One)

#### Option A: Docker Compose (Recommended)

The repository includes a production `Dockerfile` and `docker-compose.yml` (runs as a non-root user and persists the SQLite WAL database and logs under `./data`):

```bash
# 1. Create the data directory and start the container in the background
mkdir -p data
docker compose up -d --build

# 2. View live logs (look for "Logged in as ..." to confirm startup)
docker compose logs -f

# 3. Stop the service
docker compose down
```

#### Option B: Local Python Virtualenv + `tmux`

Ideal for running directly on a Linux host. The included scripts automatically restart the process after 10 seconds if it crashes:

```bash
# 1. Create a virtual environment and install dependencies (Python 3.10+)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Start the background tmux service (session name: ptt_bot)
bash restart_bg.sh

# 3. Follow live logs
tail -f bot.log

# 4. Stop the service
bash stop_bg.sh
```

<details>
<summary><b>Option C: Linux systemd Service (Click to expand)</b></summary>
<br>

If you prefer managing the bot with `systemd`, use the included `ptt-bot.service` template:

```bash
# 1. Copy the unit file and update User / WorkingDirectory to match your host
sudo cp ptt-bot.service /etc/systemd/system/ptt-bot.service
sudo nano /etc/systemd/system/ptt-bot.service

# 2. Reload systemd and enable on boot
sudo systemctl daemon-reload
sudo systemctl enable --now ptt-bot

# 3. Check service status
sudo systemctl status ptt-bot
```

</details>

---

## Usage Guide

Once online, the bot supports both **Personal DMs** and **Server Channels**:
- **Personal DM Tracking**: Send a direct message to the bot or use slash commands in DM. Alerts will be sent directly to your DMs (no server admin permissions required).
- **Server Channel Alerts**: A server admin runs `/channel_set` inside the target channel first. Rules added in the server will then push alerts to that channel.

### 1. Natural Language Assistant (Direct DM or `/ai`)

Talk to the bot in plain language; the ReAct agent will automatically invoke the right tools:
- `"幫我追蹤 HardwareSale 板販售的 4070S，預算 18000 元以內"` (Track 4070S on HardwareSale under 18,000 TWD)
- `"我想找台北面交、還有原廠保固的 3060Ti，預算 6500"` (Track 3060Ti with warranty and Taipei meetup under 6,500 TWD)
- `"幫我掃描目前 HardwareSale 板上還沒賣掉的 6700XT"` (Scan HardwareSale for currently unsold 6700XT posts)
- `"我想買二手 MacBook，幫我找適合的 PTT 看板並加入追蹤"` (Find PTT boards for second-hand MacBooks and start tracking)

### 2. Exact Keyword Rules (`/keyword_add`)

Fast matching that does not consume daily AI chat quotas:
- **Space means AND**: `台北 3060TI` (title must contain both terms)
- **Prefix `-` to exclude**: `3060 -ti -筆電 -礦` (matches 3060, excluding 3060Ti, laptops, and mining cards)
- **Max price (`max_price`)**: e.g., `6000` (skips posts priced above 6,000 TWD)

### 3. AI Fuzzy Semantic Rules (`/fuzzy_add`)

For criteria that require reading the full post body (e.g., warranty length, original box, meetup location):
- `description`: Describe your criteria in natural language.
- `hint_keywords` (recommended): Comma-separated pre-filter terms (e.g., `3060,3060ti`) so the LLM is only invoked when a post title matches the hint.

### 4. Scan Currently Unsold Items (`/scan_unsold`)

Don't want to wait for new posts? Run `/scan_unsold keyword: 3060TI` to scan recent posts on the board, filter out sold/closed listings via signature comment parsing and LLM review, and browse available items in a paginated view.

<details>
<summary><b>Click to expand: Full List of 24 Slash Commands</b></summary>
<br>

#### AI & Search
| Command | Key Parameters | Description |
| :--- | :--- | :--- |
| `/ai` | `prompt`, `scope` | Instruct the AI assistant in natural language to manage rules, find boards, or scan items |
| `/scan_unsold` | `keyword`, `board`, `max_per_keyword` | Back-scan recent board posts and filter for currently unsold items |
| `/search` | `keyword`, `board`, `pages` | Search recent PTT posts via native search (does not filter sold status) |

#### Exact Keywords
| Command | Key Parameters | Description |
| :--- | :--- | :--- |
| `/keyword_add` | `keyword`, `board`, `category`, `max_price` | Add an exact keyword rule (supports space AND, `-` exclusion, and max price) |
| `/keyword_edit` | `keyword_id`, `new_keyword`, `max_price` | Edit an existing keyword or budget cap (`0` clears the price limit) |
| `/keyword_remove` | `keyword`, `scope` | Remove an exact keyword rule (with autocomplete) |
| `/keyword_list` | `scope` | List all active keywords, fuzzy rules, and delivery status |
| `/keyword_clear` | `scope` | Clear all exact keywords in the selected scope |

#### AI Fuzzy Rules & Boards
| Command | Key Parameters | Description |
| :--- | :--- | :--- |
| `/fuzzy_add` | `description`, `board`, `category`, `hint_keywords` | Add a natural language AI semantic tracking rule |
| `/fuzzy_remove` | `rule`, `scope` | Remove an AI fuzzy rule |
| `/fuzzy_list` | `scope` | List active AI fuzzy rules |
| `/board_add` | `board`, `scope` | Verify and add a PTT board to monitor (initial baseline sync prevents old post floods) |
| `/board_remove` | `board`, `scope` | Remove a tracked board and its rules in the current scope |
| `/board_list` | None | Show all PTT boards currently polled by the system |

#### Channels, Account & Admin
| Command | Key Parameters | Description |
| :--- | :--- | :--- |
| `/channel_set` | `channel`, `scope` | Set the notification channel (sets DM delivery when run in DMs without arguments) |
| `/pause` / `/resume` | `scope` | Pause or resume notifications while keeping all rules intact |
| `/me` | None | View your personal rules, DM delivery status, and remaining daily quotas |
| `/forget_me` | None | Delete all your personal rules, delivery records, and conversation memory |
| `/status` | `scope` | View bot uptime, Groq queue metrics, and crawler status |
| `/check_now` | None | Trigger an immediate polling cycle (5-minute cooldown for non-admins) |
| `/push_mode` | `mode` (`keyword` / `all`) | Switch between keyword matching and all-post forwarding (owner only) |
| `/admin` | `action`, `target_id`, `tier` | Bot owner panel: view global stats, update subscriber tiers, or manage blacklists |
| `/help` | None | Display usage guide and command summary |

</details>

---

## Architecture & Technical Details

Click to expand the sections below for internal design details:

<details>
<summary><b>1. Project Structure & Crawl-Once Pipeline</b></summary>
<br>

```text
ptt-dc-bot/
├── bot.py               # Discord Client, 24 Slash commands, and background polling loop
├── config.py            # Environment config, quota constants, and input sanitization
├── storage.py           # SQLite WAL multi-tenant storage, auto-migration, and atomic backup
├── ptt_crawler.py       # Async PTT crawler, dual-layer sold detection, and multi-item slicing
├── groq_scheduler.py    # Priority queue (P0~P3) & 3-model dynamic rate-limit scheduler
├── groq_agent.py        # ReAct AI agent, tool execution, RBAC, and prompt injection guards
├── delivery.py          # Multi-tenant dispatcher, 3-day deduplication, and 18h in-place edits
├── embeds.py            # Discord Embed builder, length truncation, and pagination views
├── Dockerfile           # Python 3.11-slim image (runs as non-root user)
├── docker-compose.yml   # Container orchestration, resource limits, and ./data volume mount
├── ptt-bot.service      # Linux systemd service template
├── start_bg.sh          # tmux daemon startup script (auto-restarts on crash)
├── stop_bg.sh           # tmux stop script
├── restart_bg.sh        # tmux restart script
└── tests/               # Offline unit & security regression test suite
```

**Crawl-Once, Fan-Out Pipeline**:
1. **Active Board Aggregation**: Only boards with active subscriber rules are polled; idle boards are skipped.
2. **Single Fetch & Shared Cache**: Each active board is fetched once per cycle. Article bodies (10m TTL) and AI judgments (60m TTL) are cached in SQLite and shared across all subscribers to avoid PTT 503 rate limits.
3. **Per-Keyword Virtual Slicing**: `apply_keyword_view` isolates item-specific prices and sold status in multi-item posts.
4. **18h In-Place Tracking**: A background round-robin checker re-verifies pushed posts for 18 hours and edits the Discord embed in-place once sold.

</details>

<details>
<summary><b>2. Groq Multi-Model Scheduler & Quota Tiers</b></summary>
<br>

To stay within Groq's free-tier rate limits without blocking interactive chats, `groq_scheduler.py` implements:
- **3-Model Rotation Pool**: Distributes requests across `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, and `openai/gpt-oss-20b` for a combined capacity of ~24,000 TPM.
- **4-Level Priority Queue**:
  - `P0 (Interactive)`: User DMs and `/ai` commands (highest priority)
  - `P1 (Realtime)`: Semantic checks for new background posts
  - `P2 (Batch Scan)`: `/scan_unsold` jobs (yields capacity to P0/P1 when remaining tokens drop below 30%)
  - `P3 (Maintenance)`: Background maintenance tasks

**Default Subscriber Quotas** (background push checks do not consume personal daily quotas):

| Quota Metric | Personal DM (`friend`) | Standard Server (`free`) | Owner / Primary Server (`owner`) |
| :--- | :---: | :---: | :---: |
| Exact Keywords | 30 | 10 | 999 |
| AI Fuzzy Rules | 8 | 3 | 999 |
| Tracked Boards | 5 | 5 | 20 |
| Daily `/ai` & DM Chats | 60 / day | 20 / day | Unlimited |
| Daily `/scan_unsold` Scans | 25 / day | 8 / day | Unlimited |

</details>

<details>
<summary><b>3. Security Hardening (Prompt Injection / RBAC / SSRF / Docker)</b></summary>
<br>

- **Prompt Injection Defense**: All scraped PTT content is XML-escaped via `escape_prompt_xml` and wrapped in `<untrusted_ptt_content>` tags. An execution-layer **Intent Guard** blocks state-mutating tools (`add_*`, `remove_*`) during article reads unless the user's original prompt explicitly requested a rule change.
- **Multi-Tenant RBAC**: Non-admin server members cannot modify shared guild rules; all database mutations require a bound `subscriber_id`.
- **SSRF & Query Safety**: The crawler restricts requests strictly to `https://www.ptt.cc/bbs/` with `allow_redirects=False`; SQLite queries use parameterized placeholders and `ESCAPE '/'` on `LIKE` clauses.
- **Error Masking & Container Hardening**: User-facing error messages mask internal file paths and stack traces; the Docker container runs as UID 1000 (`botuser`) with read-only application code and `cap_drop: [ALL]`.

</details>

<details>
<summary><b>4. Troubleshooting (FAQ)</b></summary>
<br>

**Q1: I set up rules in DM, why am I not receiving DM alerts?**
Run `/me` to check your delivery status. If it shows a previous DM failure, your Discord privacy settings are blocking messages from the bot. Enable **"Allow direct messages from server members"** under Discord *User Settings -> Privacy & Safety* for the shared server, then send any DM to the bot to automatically re-enable delivery.

**Q2: Why do I get "Insufficient Permissions" when running a command in a server channel?**
Commands run inside a server channel default to modifying the **server's shared rules**, which requires administrator permissions. To manage your own personal watchlist, either **DM the bot directly** or set the command's `scope` parameter to `Personal (user)`.

**Q3: Why wasn't a matching post pushed?**
Posts are filtered out if: (1) they are older than 21 days; (2) the title or author's post-signature comments indicate the item is sold/pending; (3) the same seller reposted the same item within 3 days; or (4) the extracted price exceeds your `max_price`. Use `/search` if you want to view all posts including sold ones.

</details>

---

## Running Tests

The test suite runs completely offline using ephemeral SQLite databases and mocks (no live Discord or Groq API keys required):

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```
