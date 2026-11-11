# PTT-DC-Bot

**English** | [繁體中文](README.md)

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![Groq API](https://img.shields.io/badge/LLM-Groq%20API-f55036)](https://groq.com/)
[![Docker Image](https://img.shields.io/badge/Docker-GHCR%20Multi--Arch-2496ED?logo=docker&logoColor=white)](https://github.com/jasonxp516/ptt-dc-bot/pkgs/container/ptt-dc-bot)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An asynchronous PTT (Taiwan Bulletin Board System) board monitor and multi-platform alert bot (supporting Discord and Telegram; Email SMTP is experimental: the mail provider is built in, but there is no command to create an Email subscription yet, so no mail is sent) combining deterministic rule filtering with Groq LLM semantic reasoning. Designed specifically for fast-moving second-hand trading boards (`HardwareSale`, `MacShop`, `nb-shopping`, `Gamesale`, `MobileComm`).

---

## Core Features

1. **In-Place Strikethrough Updates (Zero Spam)**  
   Actively tracks delivered posts for up to 18 hours. Inspects post-signature author comments for "sold" / "reserved" status and updates existing Discord / Telegram messages in-place with a strikethrough.
2. **Multi-Item Post Slicing**  
   Automatically creates independent virtual item views for posts listing multiple products, isolating sold states and prices per keyword.
3. **Hardware Model vs. Price Disambiguation**  
   Shields hardware specification numbers (`RTX 3060`, `12G`, `750W`, `i5-12400`) from being misparsed as prices; supports `k`-unit conversion and budget caps.
4. **Crawl-Once, Fan-Out Architecture**  
   Fetches each active board once per cycle. Article text and AI judgment caches are shared across all subscribers to prevent PTT 503 rate-limiting.
5. **Unified Multi-Tenant Isolation (Channels + DMs)**  
   Backed by SQLite in WAL mode. Server public channels and private personal DMs run concurrently with isolated rule sets and quotas.

---

## One-Liner Quick Start

Open your terminal and run the command for your operating system to automatically provision dependencies, run the interactive setup wizard, and start the service:

### Linux / macOS
```bash
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.sh | bash
```

### Windows (PowerShell)
```powershell
irm https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.ps1 | iex
```

> **Advanced Deployment Guides**:
> - [Docker Compose & GHCR Deployment](docs/deploy-docker.md)
> - [Synology DSM (Container Manager) Guide](docs/deploy-synology.md)
> - [unRAID Docker Template Guide](docs/deploy-unraid.md)
> - [Linux systemd & tmux Daemon Guide](docs/deploy-linux.md)
> - [Privacy Policy & Data Deletion](PRIVACY.md)

---

## Visual UI & Screenshots

PTT-DC-Bot features an interactive native `/setup` dashboard—no need to memorize complex CLI arguments:

### 1. Interactive Control Panel
<p align="center">
  <img src="assets/setup_control_panel_en.svg" alt="PTT-DC-Bot /setup Interactive Dashboard" width="680"/>
</p>

### 2. Popular Boards Dropdown
<p align="center">
  <img src="assets/dropdown_boards_select_en.svg" alt="Popular Boards Multi-Select Dropdown" width="680"/>
</p>

### 3. Add Tracking Keyword Modal (Budget Ceiling & Exclusions)
<p align="center">
  <img src="assets/keyword_modal_form_en.svg" alt="Add Keyword Modal Dialog" width="560"/>
</p>

### 4. Push Notification Card Preview
<p align="center">
  <img src="assets/notification_embed_preview_en.svg" alt="Real-time Push Notification Embed Preview" width="680"/>
</p>

---

## Permissions & Privileged Intents

- **Privileged Gateway Intents**: **Message Content Intent is NOT required.**  
  Discord natively provides message content for Direct Messages (DMs), explicit `@Bot` mentions, and Slash commands.
- **Permissions Integer**: `274877975552` (View Channels, Send Messages, Embed Links, Read Message History), with Scopes `bot` and `applications.commands`.

---

## Command Quick Reference

| Command | Description |
| :--- | :--- |
| `/setup` | Opens the interactive visual UI dashboard |
| `/ai` | Converse with the ReAct AI agent to configure rules or scan stock |
| `/keyword_add` | Adds an exact keyword rule (space AND, `-` exclusion, price cap) |
| `/fuzzy_add` | Adds an AI semantic monitoring rule (warranty, location, packaging) |
| `/keyword_list` | Lists all active tracking rules and notification channels |
| `/scan_unsold` | Back-scans recent board posts for currently available stock |
| `/status` | Displays system uptime, crawler health, and Groq queue metrics |
| `/pause` / `/resume` | Pauses or resumes alert notifications |
| `/forget_me` | Permanently deletes your personal data, rules, and conversation memory |

---

## Local Testing

```bash
/tmp/ptt_venv/bin/pytest -q
/tmp/ptt_venv/bin/python tools/eval_sold.py
```

## License

This project is licensed under the [MIT License](LICENSE).
