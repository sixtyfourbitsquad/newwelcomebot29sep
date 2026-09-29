#!/bin/sh
# Back up, pull, rebuild, migrate, restart, then health-check.
set -eu
APP_DIR="${APP_DIR:-/opt/telegram-community-bot}"
cd "$APP_DIR"
echo "Creating a database backup before update"
sh "$APP_DIR/scripts/backup.sh"
if [ -d .git ]; then
  echo "Pulling the latest application version"
  git pull --ff-only
else
  echo "This install has no git metadata. Copy the new files into $APP_DIR, then rerun this command."
  exit 1
fi
if [ -f .install-meta ]; then
  # shellcheck disable=SC1091
  . ./.install-meta
fi
if [ -n "${COMPOSE_PROFILES:-}" ]; then
  export COMPOSE_PROFILES
fi
echo "Building containers"
docker compose build
echo "Running migrations"
docker compose up -d postgres redis
docker compose run --rm --no-deps app python -m database.apply_migrations
echo "Restarting services"
docker compose up -d
echo "Waiting for services"
sleep 15
if sh "$APP_DIR/scripts/health-check.sh"; then
  echo "Update complete."
  exit 0
fi
echo
echo "Health checks failed after the update."
echo "The database volume was not deleted. The newest backup is in ${BACKUP_DIR:-/var/backups/telegram-community-bot}."
echo "Recovery:"
echo "  1. Inspect logs: sudo botctl logs"
echo "  2. Restore the database only if you need the pre-update data:"
echo "     docker compose exec -T postgres pg_restore -U tg_bot -d tg_bot --clean --if-exists < BACKUP.dump"
echo "  3. Return to the previous git commit: git -C $APP_DIR checkout HEAD@{1}"
echo "  4. Rebuild and start: docker compose up -d --build"
exit 1
