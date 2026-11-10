# unRAID Docker template deployment

The GHCR image is published for AMD64 and ARM64. The following settings work
with the unRAID Docker tab and keep the database on the array or cache.

## 1. Add the container

1. Open **Docker** in the unRAID web interface and click **Add Container**.
2. Set **Name** to `ptt-dc-bot`.
3. Set **Repository** to
   `ghcr.io/jasonxp516/ptt-dc-bot:latest`.
4. Set **Restart Policy** to `unless-stopped`.

Add a **Path** mapping:

| Container path | Host path | Access |
|---|---|---|
| `/app/data` | `/mnt/user/appdata/ptt-dc-bot` | Read/Write |

Create the host directory before starting the container. It holds
`bot_data.db`, `bot.log`, and `heartbeat`.

## 2. Add environment variables

Add these **Variables**:

| Name | Value |
|---|---|
| `DISCORD_TOKEN` | Your Discord bot token |
| `GROQ_API_KEY` | Optional Groq API key |
| `TZ` | `Asia/Taipei` |
| `DB_PATH` | `/app/data/bot_data.db` |
| `LOG_PATH` | `/app/data/bot.log` |
| `HEARTBEAT_FILE` | `/app/data/heartbeat` |

Add Telegram or SMTP variables from `.env.example` if those providers are
enabled. Treat tokens as secrets and do not include them in a template shared
with other users.

## 3. Apply and verify

Click **Apply**. Open the container log and confirm the bot logs in
successfully. The health status becomes healthy when both the database and a
heartbeat newer than 600 seconds are present. A missing or stale heartbeat
causes the healthcheck to return 1.

If the container is unhealthy, check the mapped host directory permissions,
the token values, and the container log. Do not remove the data directory
while troubleshooting.

## 4. Updating

Use **Check for Updates** and recreate the container to pull a newer
`latest` image. The `/app/data` mapping preserves the SQLite database and
delivery history across image updates.

