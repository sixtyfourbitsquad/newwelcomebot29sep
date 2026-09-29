# VPS deployment

Install the bot on a fresh Ubuntu 22.04 or 24.04 server with one command. The script installs Docker, starts PostgreSQL, Redis, the app, the workers, and Caddy, then requests HTTPS certificates. You do not install Python, PostgreSQL, Redis, Nginx, or Node.js on the host.

## Before you start

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Point DNS at the VPS public IPv4 address:
   - `example.com` A record for the webhook
   - `admin.example.com` A record if you use the recommended admin subdomain
3. Know the numeric Telegram user ids for `ADMIN_USER_IDS` and `INITIAL_OWNER_ID`.

The installer stops if those names do not resolve to this server. It does not continue into a broken HTTPS setup.

## Install

Copy this project onto the server, then:

```bash
sudo bash setup.sh
```

The script asks for:

1. Domain name
2. Telegram bot token (input is hidden)
3. Telegram bot username
4. `ADMIN_USER_IDS`
5. `INITIAL_OWNER_ID`
6. PostgreSQL password (hidden, or generated if you press Enter)
7. Optional Sentry DSN (hidden)
8. Monitoring choices: JSON logs, a metrics token, and how many days of backups to keep
9. Whether to enable the web admin panel

The recommended admin address is `https://admin.example.com`. You can instead choose `https://example.com/admin`.

Review the non-secret settings and confirm before Docker is installed. Running the script again is safe. It keeps the existing database volume. It does not delete `.env` unless you say so, and it stores a timestamped `.env` backup first.

For a repeat run with a prepared env file:

```bash
sudo bash setup.sh --non-interactive --env-file .env
```

If `/opt/telegram-community-bot/.env` already exists, that file is kept.

## What gets installed

Files live in `/opt/telegram-community-bot`.

| Service | Role |
|---|---|
| Caddy | HTTPS on ports 80 and 443, including certificate renewal |
| app | Telegram webhook API |
| admin | Web admin panel, when enabled |
| broadcast-worker | Broadcast queue |
| scheduler-worker | Scheduled messages |
| onboarding-worker | Post-start drip |
| retention-worker | Come-back messages |
| PostgreSQL | Database, not published to the internet |
| Redis | Queues and locks, not published to the internet |

The firewall allows SSH, HTTP, and HTTPS. PostgreSQL `5432` and Redis `6379` are not published. Containers restart unless you stop them.

Generated secrets (`WEBHOOK_SECRET`, `WEB_SESSION_SECRET`, `TELEGRAM_WEBHOOK_SECRET_TOKEN`, `INTERNAL_ENCRYPTION_SECRET`) are written to `.env` with mode `600`. The installer does not print them. `DEPLOYMENT.txt` lists hostnames and the backup directory only.

## Telegram Login Widget

After install, if the web panel is enabled, the script prints the hostname to give BotFather. Run `/setdomain` and enter that hostname, for example:

```text
admin.example.com
```

The webhook itself is registered automatically. Its secret stays in `.env`.

## Commands

```bash
sudo botctl status
sudo botctl logs
sudo botctl restart
sudo botctl update
sudo botctl backup
sudo botctl migrate
sudo botctl shell
sudo botctl uninstall
```

`botctl update` backs up the database, pulls the git checkout, rebuilds the images, runs migrations, and health-checks. If the checks fail, the database volume is left in place and the script prints how to restore the backup.

`botctl uninstall` asks for a backup, then stops containers. It does not delete database volumes unless you type `DELETE DATA`.

## Backups

A systemd timer runs `scripts/backup.sh` every day. Files are mode `600` under `/var/backups/telegram-community-bot`. The default history is 7 days.

Manual backup:

```bash
sudo /opt/telegram-community-bot/scripts/backup.sh
```

Restore a dump into the running database:

```bash
cd /opt/telegram-community-bot
docker compose exec -T postgres pg_restore -U tg_bot -d tg_bot --clean --if-exists < /var/backups/telegram-community-bot/tg_bot-TIMESTAMP.dump
```

## Health checks

`scripts/health-check.sh` checks the containers, PostgreSQL, Redis, HTTPS, the health URL, the bot token, and that Telegram has a webhook on your domain. It does not print the token or the webhook secret.

## If setup fails

The error names the step that failed. Volumes are not removed. Useful checks:

```bash
sudo botctl logs
sudo botctl status
dig +short example.com A
```

Fix DNS or the setting that failed, then run `sudo bash setup.sh` again.
