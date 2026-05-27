# SKYDOVPSBOT

A Python Discord VPS deploy bot using Docker SDK, SQLite, and slash commands.

## Repository structure

- `dockerbot/`
  - `main.py` — package entrypoint for the bot
  - `bot.py` — Discord command setup and bot lifecycle
  - `config.py` — configuration loader and helpers
  - `db.py` — SQLite database initialization and helpers
  - `docker_ops.py` — deploy/delete/restart container logic
  - `logging.py` — structured logging setup
  - `config.cfg` — bot configuration (token, admins, defaults)
  - `config.cfg.example` — example config template
  - `requirements.txt` — Python dependencies
  - `install.sh` — installer script for system packages and service setup

## Quick start

1. Copy `dockerbot/config.cfg.example` to `dockerbot/config.cfg`.
2. Set your Discord bot token and admin IDs.
3. Run the installer locally:
   ```bash
   sudo bash dockerbot/install.sh
   ```
4. Or install directly from GitHub with one command:
   ```bash
   bash <(curl -fsSL https://raw.githubusercontent.com/ggghosain0-ux/vpsbot/main/dockerbot/install.sh)
   ```
5. The bot will be started as a systemd service.

## Notes

- `dockerbot/main.py` is the executable entrypoint.
- Logs are written to `logs/vpsbot.log`.
- The SQLite database is stored at `data.db` by default.
- Deploys can now include an expiry using `expiry_days` and `expiry_hours`, and expired VPS records are cleaned up automatically.
