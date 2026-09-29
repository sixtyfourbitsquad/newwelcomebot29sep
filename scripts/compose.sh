#!/bin/sh
set -eu
APP_DIR="${APP_DIR:-/opt/telegram-community-bot}"
cd "$APP_DIR"
if [ -f .install-meta ]; then
  # shellcheck disable=SC1091
  . ./.install-meta
fi
if [ -n "${COMPOSE_PROFILES:-}" ]; then
  export COMPOSE_PROFILES
fi
exec docker compose "$@"
