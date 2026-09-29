#!/bin/sh
# Stop containers. Database volumes stay unless the operator types DELETE DATA.
set -eu
APP_DIR="${APP_DIR:-/opt/telegram-community-bot}"
cd "$APP_DIR"
echo "This stops the bot. PostgreSQL and Redis data are kept."
printf 'Create a backup before stopping? [Y/n] '
read -r backup_answer
case "${backup_answer:-Y}" in
  n|N|no|NO) ;;
  *) sh "$APP_DIR/scripts/backup.sh" ;;
esac
printf 'Type uninstall to continue: '
read -r confirm
if [ "$confirm" != "uninstall" ]; then
  echo "Cancelled."
  exit 1
fi
if [ -f .install-meta ]; then
  # shellcheck disable=SC1091
  . ./.install-meta
fi
if [ -n "${COMPOSE_PROFILES:-}" ]; then
  export COMPOSE_PROFILES
fi
docker compose down
rm -f /usr/local/bin/botctl
systemctl disable --now telegram-bot-backup.timer >/dev/null 2>&1 || true
echo "Containers stopped. Volumes pgdata, redisdata, and caddy_data are still on disk."
echo "Backups remain in ${BACKUP_DIR:-/var/backups/telegram-community-bot}."
printf 'Type DELETE DATA to remove Docker volumes too, or press Enter to keep them: '
read -r wipe
if [ "$wipe" = "DELETE DATA" ]; then
  docker compose down -v
  echo "Docker volumes removed."
else
  echo "Volumes kept. To remove them later: cd $APP_DIR && docker compose down -v"
fi
