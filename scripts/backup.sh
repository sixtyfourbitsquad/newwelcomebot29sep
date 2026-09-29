#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
root="${BACKUP_DIR:-/var/backups/welcome-bot}"
mkdir -p "$root/daily" "$root/weekly"
exec 9>"$root/.lock"
flock -n 9 || exit 0
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tmp="$root/daily/$stamp.dump.partial"
trap 'rm -f -- "$tmp"' EXIT
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$tmp"
docker compose exec -T postgres pg_restore --list < "$tmp" > /dev/null
mv "$tmp" "$root/daily/$stamp.dump"
if [[ "$(date -u +%u)" == 7 ]]; then cp "$root/daily/$stamp.dump" "$root/weekly/$stamp.dump"; fi
# Keep newest seven daily and four Sunday backups, regardless of missed runs.
find "$root/daily" -maxdepth 1 -name '*.dump' -printf '%f\n' | sort -r | tail -n +8 | while IFS= read -r name; do rm -- "$root/daily/$name"; done
find "$root/weekly" -maxdepth 1 -name '*.dump' -printf '%f\n' | sort -r | tail -n +5 | while IFS= read -r name; do rm -- "$root/weekly/$name"; done
if [[ -n "${BACKUP_S3_URI:-}" ]]; then
  aws s3 cp "$root/daily/$stamp.dump" "${BACKUP_S3_URI%/}/daily/$stamp.dump" --sse AES256
  if [[ "$(date -u +%u)" == 7 ]]; then aws s3 cp "$root/weekly/$stamp.dump" "${BACKUP_S3_URI%/}/weekly/$stamp.dump" --sse AES256; fi
fi
echo "Backup complete: $stamp"
