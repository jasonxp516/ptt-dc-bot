# Linux systemd and tmux daemon deployment

This guide covers native Python operation on a Linux VPS or always-on host.
Use systemd for a machine service; use the tmux scripts when you want an
interactive session that survives SSH disconnects.

## Prepare the application

From the repository directory, create a virtual environment and install
dependencies:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Edit `.env` with the notification credentials. Keep the project directory
owned by the service user and ensure it can write the database and log paths.

## systemd (recommended)

The repository includes `ptt-bot.service`. Copy it, then edit `User`,
`WorkingDirectory`, `EnvironmentFile`, `ExecStart`, and log paths to match the
installation:

```bash
sudo cp ptt-bot.service /etc/systemd/system/ptt-bot.service
sudoedit /etc/systemd/system/ptt-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now ptt-bot
sudo systemctl status ptt-bot
journalctl -u ptt-bot -f
```

Set `HEARTBEAT_FILE` in `.env` if you want an explicit location; otherwise the
bot places it beside the configured database. `Restart=always` and
`RestartSec=10` provide process recovery. Use `sudo systemctl stop ptt-bot`
for a clean shutdown.

## tmux scripts

Install `tmux`, then run the scripts from the repository:

```bash
bash start_bg.sh
tmux attach -t ptt_bot
bash stop_bg.sh
bash restart_bg.sh
```

`start_bg.sh` creates the `ptt_bot` session and restarts the bot after an
unexpected exit with a 10-second delay. Output is appended to
`tmux_console.log`; detach with `Ctrl-b d`. `stop_bg.sh` kills the session and
cleans up matching bot processes, while `restart_bg.sh` performs stop/start.
Use only one daemon method at a time.

