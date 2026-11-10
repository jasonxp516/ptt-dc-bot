# Synology DSM Container Manager deployment

Synology DSM 7.x can run the published multi-architecture image through
Container Manager. This guide uses a Compose project so updates and persistent
storage remain easy to manage.

## 1. Create the folders

In **File Station**, create:

```text
/docker/ptt-dc-bot/
├── docker-compose.yml
├── .env
└── data/
```

Download `docker-compose.yml` and `.env.example` from the repository, rename
the template to `.env`, and edit it with the Discord token and optional Groq,
Telegram, or SMTP credentials. Keep `.env` private. The `data` directory must
be writable because it stores SQLite, logs, and the heartbeat file.

## 2. Create the project

1. Open **Container Manager** and choose **Project** → **Create**.
2. Set the project name to `ptt-dc-bot`.
3. Select `/docker/ptt-dc-bot` as the project path.
4. Choose **Create docker-compose.yml** (or import the downloaded file) and
   confirm that the `env_file` points to `.env`.
5. Finish the wizard. Container Manager pulls
   `ghcr.io/jasonxp516/ptt-dc-bot:latest` and starts the project.

The Compose file maps `./data` to `/app/data`, sets the database, log, and
heartbeat paths, and applies the container resource and capability limits.

## 3. Verify operation

Open the project’s container **Details** → **Log** and look for the successful
Discord login message. In **Health**, the status should become `healthy` after
the bot has written its heartbeat. The check requires a database and a
heartbeat modified within the last 600 seconds.

If the status is unhealthy, confirm that `data/bot_data.db` and
`data/heartbeat` exist and that the shared folder permissions allow container
writes. Check the project log for invalid tokens or missing environment
variables.

## 4. Updates and shutdown

Use **Project** → **Action** → **Pull** (or recreate the project) to fetch a
new image, then start it again. Stop the project from Container Manager when
needed. Do not delete the `data` folder unless you intentionally want to
remove the database and delivery history.

