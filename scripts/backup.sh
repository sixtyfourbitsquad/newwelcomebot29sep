#!/bin/sh
# Daily or manual PostgreSQL backup. Does not print secrets.
set -eu
APP_DIR="${APP_DIR:-/opt/telegram-community-bot}"
cd "$APP_DIR"
if [ -f .install-meta ]; then
  # shellcheck disable=SC1091
  . ./.install-meta
fi
BACKUP_DIR="${BACKUP_DIR:-/var/backups/telegram-community-bot}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_DIR/tg_bot-${STAMP}.dump"
echo "Writing backup to $OUT"
if [ -n "${COMPOSE_PROFILES:-}" ]; then
  export COMPOSE_PROFILES
fi
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-tg_bot}" -d "${POSTGRES_DB:-tg_bot}" -Fc > "$OUT"
chmod 600 "$OUT"
find "$BACKUP_DIR" -type f -name 'tg_bot-*.dump' -mtime +"$BACKUP_RETENTION_DAYS" -delete
echo "Backup complete: $OUT"
echo "Restore example:"
echo "  docker compose exec -T postgres pg_restore -U ${POSTGRES_USER:-tg_bot} -d ${POSTGRES_DB:-tg_bot} --clean --if-exists < $OUT"
