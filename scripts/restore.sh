#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ $# == 2 && "$2" == --replace-database ]] || { echo 'Usage: restore.sh /absolute/backup.dump --replace-database'; exit 2; }
backup="$(realpath "$1")"
[[ -s "$backup" ]] || { echo 'Backup missing or empty'; exit 2; }
docker compose exec -T postgres pg_restore --list < "$backup" > /dev/null
docker compose stop app worker broadcast-worker
docker compose exec -T postgres sh -c 'dropdb --force --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"; createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose exec -T postgres sh -c 'pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$backup"
echo 'Restored. Inspect data before running docker compose up -d. Old queue signals are harmless; job state lives in PostgreSQL.'
