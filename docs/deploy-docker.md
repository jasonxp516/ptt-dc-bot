# Docker Compose and GHCR deployment

This is the recommended deployment for Linux servers, Raspberry Pi, and NAS
systems. The published image is multi-architecture and supports AMD64 and
ARM64:

```text
ghcr.io/jasonxp516/ptt-dc-bot:latest
```

## 1. Prepare the project

Create a directory with a persistent `data` directory, then download the
Compose file and environment template:

```bash
mkdir -p ptt-dc-bot/data
cd ptt-dc-bot
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/docker-compose.yml \
  -o docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/.env.example -o .env
chmod 600 .env
```

Edit `.env` and add at least one notification provider. Discord is the normal
minimum configuration; `GROQ_API_KEY` is optional but enables natural-language
chat and semantic sold-item filtering:

```ini
DISCORD_TOKEN=your_discord_bot_token
GROQ_API_KEY=your_groq_api_key
```

Telegram and SMTP settings can be added from `.env.example`. Never commit
`.env` or paste its values into support logs.

## 2. Start and operate the service

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs -f ptt-dc-bot
```

The default Compose file uses `restart: unless-stopped`, drops Linux
capabilities, enables `no-new-privileges`, limits the container to 1 CPU and
512 MB RAM, and persists the SQLite database and logs with
`./data:/app/data`. Stop it with `docker compose down`; the data directory is
not removed.

## 3. Heartbeat healthcheck

The bot writes a Unix timestamp to the configured `HEARTBEAT_FILE` during
startup and during polling. Compose checks that both
`/app/data/bot_data.db` and the heartbeat exist and that the heartbeat file was
modified less than 600 seconds ago. A healthy container exits the Python
healthcheck command with status 0; a missing database, missing heartbeat, or
stale heartbeat exits with status 1. This catches a stopped or wedged bot even
when the container process itself still exists:

```bash
docker inspect --format='{{json .State.Health}}' ptt-dc-bot
```

The default environment values are:

```yaml
DB_PATH=/app/data/bot_data.db
LOG_PATH=/app/data/bot.log
HEARTBEAT_FILE=/app/data/heartbeat
```

Keep the `data` bind mount writable by the container user. If a host security
profile prevents writes, fix the directory ownership/label rather than
disabling the heartbeat check.

## 4. Updating and troubleshooting

Pull and recreate the container to update:

```bash
docker compose pull
docker compose up -d
docker compose logs --tail=100 ptt-dc-bot
```

If the health status is `unhealthy`, inspect the logs first, then verify that
`data/bot_data.db` and `data/heartbeat` are present and that the host clock is
correct. `docker compose config` validates the rendered Compose configuration.

