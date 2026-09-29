#!/usr/bin/env bash
# One-command installer for a fresh Ubuntu 22.04 or 24.04 VPS.
# Usage: sudo bash setup.sh
#        sudo bash setup.sh --non-interactive --env-file /path/to/.env
set -euo pipefail

APP_DIR=/opt/telegram-community-bot
BACKUP_DIR=/var/backups/telegram-community-bot
NONINTERACTIVE=0
ENV_FILE_SRC=""
KEEP_DB_PASSWORD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --non-interactive) NONINTERACTIVE=1 ;;
    --env-file)
      ENV_FILE_SRC="${2:-}"
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
  shift
done

trap 'echo "Setup failed near line $LINENO. Database volumes were not deleted." >&2' ERR

info() { printf '%s\n' "$*"; }
die() { printf '%s\n' "$*" >&2; exit 1; }

require_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run this script as root: sudo bash setup.sh"
}

require_ubuntu() {
  [[ -f /etc/os-release ]] || die "Cannot detect the operating system."
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || die "This installer supports Ubuntu 22.04 and 24.04. Detected: ${ID:-unknown}."
  case "${VERSION_ID:-}" in
    22.04|24.04) ;;
    *) die "This installer supports Ubuntu 22.04 and 24.04. Detected: ${VERSION_ID:-unknown}." ;;
  esac
  info "Operating system: Ubuntu $VERSION_ID"
}

prompt() {
  local label="$1" secret="${2:-0}" value=""
  if [[ "$secret" -eq 1 ]]; then
    # IFS= keeps a pasted token intact. The newline goes to the terminal, not into the value.
    IFS= read -r -s -p "$label" value || true
    printf '\n' >&2
  else
    IFS= read -r -p "$label" value || true
  fi
  value=${value//$'\r'/}
  printf '%s' "$value"
}

trim_edges() {
  local value="$1"
  value=${value#"${value%%[![:space:]]*}"}
  value=${value%"${value##*[![:space:]]}"}
  printf '%s' "$value"
}

telegram_token_accepted() {
  local token="$1" body
  body=$(curl -sS --max-time 20 "https://api.telegram.org/bot${token}/getMe" 2>/dev/null) || true
  [[ -n "$body" ]] || return 2
  printf '%s' "$body" | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'
}

valid_domain() {
  [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]]
}

valid_ids() {
  [[ "$1" =~ ^[0-9]+(,[0-9]+)*$ ]]
}

public_ip() {
  curl -4 -fsS --max-time 15 https://api.ipify.org || curl -4 -fsS --max-time 15 https://ifconfig.me
}

domain_points_here() {
  local domain="$1" ip="$2" resolved
  resolved=$(getent ahostsv4 "$domain" | awk '{print $1}' | sort -u | tr '\n' ' ')
  [[ " $resolved " == *" $ip "* ]]
}

rand_hex() {
  openssl rand -hex 32
}

set_kv() {
  local file="$1" key="$2" value="$3" tmp
  tmp=$(mktemp)
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' "$file" > "$tmp"
  else
    cp "$file" "$tmp"
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  chmod 600 "$tmp"
  mv "$tmp" "$file"
  chmod 600 "$file"
}

env_value() {
  local key="$1" file="$2"
  sed -n "s/^${key}=//p" "$file" | head -n 1
}

write_caddy() {
  local webhook_host="$1" admin_host="$2" mode="$3" email="$4" dest="$APP_DIR/Caddyfile" tmp
  tmp=$(mktemp)
  {
    if [[ -n "$email" ]]; then
      printf '{\n\temail %s\n}\n\n' "$email"
    fi
    if [[ "$mode" == "subdomain" ]]; then
      printf '%s {\n\treverse_proxy app:8000\n}\n\n' "$webhook_host"
      printf '%s {\n\treverse_proxy admin:8000\n}\n' "$admin_host"
    elif [[ "$mode" == "path" ]]; then
      cat <<EOF
${webhook_host} {
	handle /admin* {
		reverse_proxy admin:8000
	}
	handle /panel* {
		reverse_proxy admin:8000
	}
	handle {
		reverse_proxy app:8000
	}
}
EOF
    else
      printf '%s {\n\treverse_proxy app:8000\n}\n' "$webhook_host"
    fi
  } > "$tmp"
  mv "$tmp" "$dest"
}

write_env() {
  local dest="$1" tmp
  tmp=$(mktemp)
  {
    printf 'BOT_TOKEN=%s\n' "$BOT_TOKEN"
    printf 'TELEGRAM_LOGIN_BOT_USERNAME=%s\n' "$BOT_USERNAME"
    printf 'WEBHOOK_BASE_URL=%s\n' "$WEBHOOK_BASE_URL"
    printf 'WEBHOOK_HOST=%s\n' "$WEBHOOK_HOST"
    printf 'WEBHOOK_PATH=/tg/webhook/{secret}\n'
    printf 'WEBHOOK_SECRET=%s\n' "$WEBHOOK_SECRET"
    printf 'TELEGRAM_WEBHOOK_SECRET_TOKEN=%s\n' "$TELEGRAM_WEBHOOK_SECRET_TOKEN"
    printf 'WEBHOOK_REQUIRE_SECRET_TOKEN=true\n'
    printf 'WEBHOOK_REGISTER_ON_STARTUP=true\n'
    printf 'ADMIN_USER_IDS=%s\n' "$ADMIN_USER_IDS"
    printf 'INITIAL_OWNER_ID=%s\n' "$INITIAL_OWNER_ID"
    printf 'POSTGRES_USER=tg_bot\n'
    printf 'POSTGRES_PASSWORD=%s\n' "$POSTGRES_PASSWORD"
    printf 'POSTGRES_DB=tg_bot\n'
    printf 'POSTGRES_DSN=postgresql://tg_bot:%s@postgres:5432/tg_bot\n' "$POSTGRES_PASSWORD"
    printf 'REDIS_URL=redis://redis:6379/0\n'
    printf 'RUN_EMBEDDED_WORKERS=false\n'
    printf 'WEB_ADMIN_ENABLED=%s\n' "$WEB_ADMIN_ENABLED"
    printf 'ADMIN_PANEL_URL=%s\n' "$ADMIN_PANEL_URL"
    printf 'ADMIN_HOST=%s\n' "$ADMIN_HOST"
    printf 'WEB_SESSION_SECRET=%s\n' "$WEB_SESSION_SECRET"
    printf 'WEB_SESSION_EXPIRE_HOURS=24\n'
    printf 'WEB_COOKIE_SECURE=true\n'
    printf 'WEB_COOKIE_SAMESITE=lax\n'
    printf 'INTERNAL_ENCRYPTION_SECRET=%s\n' "$INTERNAL_ENCRYPTION_SECRET"
    printf 'SENTRY_DSN=%s\n' "$SENTRY_DSN"
    printf 'LOG_JSON=%s\n' "$LOG_JSON"
    printf 'METRICS_TOKEN=%s\n' "$METRICS_TOKEN"
    printf 'HOST=0.0.0.0\n'
    printf 'PORT=8000\n'
    printf 'LOG_LEVEL=INFO\n'
    printf 'ONBOARDING_DRIP_ENABLED=true\n'
    printf 'COMPOSE_PROJECT_NAME=telegram-community-bot\n'
    printf 'ADMIN_MODE=%s\n' "$ADMIN_MODE"
    printf 'ACME_EMAIL=%s\n' "${ACME_EMAIL:-}"
    printf 'BACKUP_RETENTION_DAYS=%s\n' "${BACKUP_RETENTION_DAYS:-7}"
  } > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$dest"
  chmod 600 "$dest"
}

install_base_tools() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl openssl git ufw
}

install_docker() {
  if docker compose version >/dev/null 2>&1; then
    info "Docker Compose is already installed."
    systemctl enable --now docker
    return
  fi
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

copy_app() {
  local source_dir
  source_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  mkdir -p "$APP_DIR"
  if [[ "$source_dir" == "$APP_DIR" ]]; then
    info "Using application files in $APP_DIR"
    return
  fi
  info "Copying application files to $APP_DIR"
  tar -C "$source_dir" \
    --exclude .env \
    --exclude storage \
    --exclude logs \
    --exclude backups \
    --exclude DEPLOYMENT.txt \
    --exclude .install-meta \
    -cf - . | tar -C "$APP_DIR" -xf -
}

configure_firewall() {
  ufw allow OpenSSH || true
  ufw allow 22/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
  info "Firewall allows SSH, HTTP, and HTTPS. PostgreSQL and Redis are not published."
}

ask_settings() {
  local domain style answer status
  while true; do
    domain=$(prompt "Domain name (example.com): ")
    domain=$(printf '%s' "$domain" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
    valid_domain "$domain" && break
    info "Enter a domain such as example.com"
  done
  WEBHOOK_HOST="$domain"
  WEBHOOK_BASE_URL="https://${domain}"

  ACME_EMAIL=$(prompt "Let's Encrypt email (optional, press Enter to skip): ")
  if [[ -n "$ACME_EMAIL" && ! "$ACME_EMAIL" =~ ^[^[:space:]]+@[^[:space:]]+$ ]]; then
    die "Enter an email such as you@example.com, or leave it empty."
  fi

  while true; do
    BOT_TOKEN=$(prompt "Telegram bot token: " 1)
    BOT_TOKEN=$(trim_edges "$BOT_TOKEN")
    if [[ ! "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
      info "Enter the token from BotFather: a numeric bot id, a colon, then the secret."
      continue
    fi
    info "Checking the token with Telegram"
    telegram_token_accepted "$BOT_TOKEN" && status=0 || status=$?
    if [[ "$status" -eq 0 ]]; then
      break
    fi
    if [[ "$status" -eq 2 ]]; then
      info "Could not reach Telegram to check the token. Try again."
      continue
    fi
    info "Telegram rejected this token. Generate a new token with BotFather."
  done

  while true; do
    BOT_USERNAME=$(prompt "Telegram bot username (without @): ")
    BOT_USERNAME="${BOT_USERNAME#@}"
    [[ "$BOT_USERNAME" =~ ^[A-Za-z0-9_]{4,32}$ ]] && break
    info "Enter the bot username, for example my_community_bot"
  done

  while true; do
    ADMIN_USER_IDS=$(prompt "ADMIN_USER_IDS (comma-separated numeric Telegram ids): ")
    ADMIN_USER_IDS=${ADMIN_USER_IDS// /}
    valid_ids "$ADMIN_USER_IDS" && break
    info "Use digits separated by commas, for example 111,222"
  done

  while true; do
    INITIAL_OWNER_ID=$(prompt "INITIAL_OWNER_ID (numeric Telegram id): ")
    [[ "$INITIAL_OWNER_ID" =~ ^[0-9]+$ ]] && break
    info "The owner id must be a number."
  done
  if [[ ",${ADMIN_USER_IDS}," != *",${INITIAL_OWNER_ID},"* ]]; then
    info "The owner id is not in ADMIN_USER_IDS. It will be added so they receive the shared inbox."
    ADMIN_USER_IDS="${ADMIN_USER_IDS},${INITIAL_OWNER_ID}"
  fi

  if [[ "${KEEP_DB_PASSWORD:-0}" -eq 1 ]]; then
    info "An existing database was found. The current database password will be kept."
  else
    while true; do
      POSTGRES_PASSWORD=$(prompt "PostgreSQL password (Enter to generate one): " 1)
      if [[ -z "$POSTGRES_PASSWORD" ]]; then
        POSTGRES_PASSWORD=$(openssl rand -hex 24)
        info "A database password was generated and will be stored only in .env"
        break
      fi
      [[ "$POSTGRES_PASSWORD" =~ ^[A-Za-z0-9]{12,}$ ]] && break
      info "Use at least 12 letters or numbers, or press Enter to generate one."
    done
  fi

  SENTRY_DSN=$(prompt "Sentry DSN (optional, press Enter to skip): " 1)
  if [[ -n "$SENTRY_DSN" && ! "$SENTRY_DSN" =~ ^https:// ]]; then
    die "Sentry DSN must start with https:// or be left empty."
  fi

  BACKUP_RETENTION_DAYS=$(prompt "Backup retention in days [7]: ")
  BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-7}
  [[ "$BACKUP_RETENTION_DAYS" =~ ^[0-9]+$ ]] || die "Backup retention must be a number."

  answer=$(prompt "Enable JSON logs and a metrics token? [y/N]: ")
  case "$answer" in
    y|Y|yes|YES)
      LOG_JSON=true
      METRICS_TOKEN=$(rand_hex)
      ;;
    *)
      LOG_JSON=false
      METRICS_TOKEN=""
      ;;
  esac

  answer=$(prompt "Enable the web admin panel? [Y/n]: ")
  case "$answer" in
    n|N|no|NO)
      WEB_ADMIN_ENABLED=false
      ADMIN_MODE=none
      ADMIN_HOST=""
      ADMIN_PANEL_URL=""
      ;;
    *)
      WEB_ADMIN_ENABLED=true
      info "Admin panel address:"
      info "  1) https://admin.${domain}    (recommended)"
      info "  2) https://${domain}/admin"
      style=$(prompt "Choose 1 or 2 [1]: ")
      style=${style:-1}
      if [[ "$style" == "2" ]]; then
        ADMIN_MODE=path
        ADMIN_HOST="$domain"
        ADMIN_PANEL_URL="https://${domain}/admin"
      else
        ADMIN_MODE=subdomain
        ADMIN_HOST="admin.${domain}"
        ADMIN_PANEL_URL="https://admin.${domain}"
      fi
      ;;
  esac

  WEBHOOK_SECRET=$(rand_hex)
  TELEGRAM_WEBHOOK_SECRET_TOKEN=$(rand_hex)
  WEB_SESSION_SECRET=$(rand_hex)
  INTERNAL_ENCRYPTION_SECRET=$(rand_hex)

  info
  info "Review (secrets are hidden):"
  info "  Webhook host:      $WEBHOOK_BASE_URL"
  info "  Admin panel:       ${ADMIN_PANEL_URL:-disabled}"
  info "  Bot username:      @${BOT_USERNAME}"
  info "  Admin ids:         $ADMIN_USER_IDS"
  info "  Owner id:          $INITIAL_OWNER_ID"
  info "  Sentry:            $([[ -n $SENTRY_DSN ]] && echo set || echo off)"
  info "  JSON logs:         $LOG_JSON"
  info "  Metrics token:     $([[ -n $METRICS_TOKEN ]] && echo set || echo off)"
  info "  Backup retention:  ${BACKUP_RETENTION_DAYS} days"
  answer=$(prompt "Install with these settings? [y/N]: ")
  case "$answer" in
    y|Y|yes|YES) ;;
    *) info "Cancelled. Docker, the firewall, and HTTPS were not configured."; exit 0 ;;
  esac
}

load_existing_env() {
  local file="$1"
  # shellcheck disable=SC1090
  set -a
  # shellcheck disable=SC1090
  . "$file"
  set +a
  WEBHOOK_HOST="${WEBHOOK_HOST:-}"
  if [[ -z "$WEBHOOK_HOST" && -n "${WEBHOOK_BASE_URL:-}" ]]; then
    WEBHOOK_HOST="${WEBHOOK_BASE_URL#https://}"
    WEBHOOK_HOST="${WEBHOOK_HOST#http://}"
    WEBHOOK_HOST="${WEBHOOK_HOST%%/*}"
  fi
  ADMIN_HOST="${ADMIN_HOST:-}"
  if [[ -z "$ADMIN_HOST" && -n "${ADMIN_PANEL_URL:-}" ]]; then
    ADMIN_HOST="${ADMIN_PANEL_URL#https://}"
    ADMIN_HOST="${ADMIN_HOST#http://}"
    ADMIN_HOST="${ADMIN_HOST%%/*}"
  fi
  BOT_USERNAME="${TELEGRAM_LOGIN_BOT_USERNAME:-${BOT_USERNAME:-}}"
  WEB_ADMIN_ENABLED="${WEB_ADMIN_ENABLED:-false}"
  ADMIN_PANEL_URL="${ADMIN_PANEL_URL:-}"
  SENTRY_DSN="${SENTRY_DSN:-}"
  LOG_JSON="${LOG_JSON:-false}"
  METRICS_TOKEN="${METRICS_TOKEN:-}"
  BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
  ACME_EMAIL="${ACME_EMAIL:-}"
  if [[ "$WEB_ADMIN_ENABLED" == "true" && "$ADMIN_PANEL_URL" == */admin ]]; then
    ADMIN_MODE=path
  elif [[ "$WEB_ADMIN_ENABLED" == "true" ]]; then
    ADMIN_MODE=subdomain
  else
    ADMIN_MODE=none
  fi
}

fill_generated_secrets() {
  local file="$1"
  [[ -n "${WEBHOOK_SECRET:-}" ]] || WEBHOOK_SECRET=$(rand_hex)
  [[ -n "${TELEGRAM_WEBHOOK_SECRET_TOKEN:-}" ]] || TELEGRAM_WEBHOOK_SECRET_TOKEN=$(rand_hex)
  [[ -n "${WEB_SESSION_SECRET:-}" ]] || WEB_SESSION_SECRET=$(rand_hex)
  [[ -n "${INTERNAL_ENCRYPTION_SECRET:-}" ]] || INTERNAL_ENCRYPTION_SECRET=$(rand_hex)
  set_kv "$file" WEBHOOK_SECRET "$WEBHOOK_SECRET"
  set_kv "$file" TELEGRAM_WEBHOOK_SECRET_TOKEN "$TELEGRAM_WEBHOOK_SECRET_TOKEN"
  set_kv "$file" WEB_SESSION_SECRET "$WEB_SESSION_SECRET"
  set_kv "$file" INTERNAL_ENCRYPTION_SECRET "$INTERNAL_ENCRYPTION_SECRET"
  set_kv "$file" WEBHOOK_REQUIRE_SECRET_TOKEN true
  set_kv "$file" RUN_EMBEDDED_WORKERS false
  if [[ "${POSTGRES_PASSWORD:-}" =~ ^[A-Za-z0-9]+$ ]]; then
    set_kv "$file" POSTGRES_DSN "postgresql://${POSTGRES_USER:-tg_bot}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-tg_bot}"
  fi
  set_kv "$file" REDIS_URL "redis://redis:6379/0"
  set_kv "$file" WEBHOOK_HOST "$WEBHOOK_HOST"
  set_kv "$file" ADMIN_HOST "$ADMIN_HOST"
  chmod 600 "$file"
}

check_dns() {
  local ip
  ip=$(public_ip) || die "Could not detect this server's public IPv4 address."
  info "This server's public IPv4: $ip"
  if ! domain_points_here "$WEBHOOK_HOST" "$ip"; then
    die "DNS for ${WEBHOOK_HOST} does not point at ${ip}. Create an A record for ${WEBHOOK_HOST} and run setup again. HTTPS was not started."
  fi
  if [[ "$ADMIN_MODE" == "subdomain" && "$ADMIN_HOST" != "$WEBHOOK_HOST" ]]; then
    if ! domain_points_here "$ADMIN_HOST" "$ip"; then
      die "DNS for ${ADMIN_HOST} does not point at ${ip}. Create an A record for ${ADMIN_HOST} and run setup again. HTTPS was not started."
    fi
  fi
  info "DNS points at this server."
}

prepare_config() {
  mkdir -p "$APP_DIR"
  copy_app
  mkdir -p "$APP_DIR/storage" "$APP_DIR/logs" "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  chown -R 1000:1000 "$APP_DIR/storage" "$APP_DIR/logs" || true

  local keep=0
  if [[ -f "$APP_DIR/.env" ]]; then
    if [[ "$NONINTERACTIVE" -eq 1 ]]; then
      info "Keeping the existing .env"
      keep=1
    else
      local answer
      answer=$(prompt "An existing .env was found. Keep it? [Y/n]: ")
      case "$answer" in
        n|N|no|NO) keep=0 ;;
        *) keep=1 ;;
      esac
    fi
  elif [[ "$NONINTERACTIVE" -eq 1 ]]; then
    [[ -n "$ENV_FILE_SRC" && -f "$ENV_FILE_SRC" ]] || die "Non-interactive mode needs --env-file pointing at an existing env file."
    cp "$ENV_FILE_SRC" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    keep=1
  fi

  if [[ "$keep" -eq 1 ]]; then
    load_existing_env "$APP_DIR/.env"
    [[ -n "${BOT_TOKEN:-}" && -n "${WEBHOOK_BASE_URL:-}" && -n "${ADMIN_USER_IDS:-}" && -n "${POSTGRES_PASSWORD:-}" ]] || die "The existing .env is missing BOT_TOKEN, WEBHOOK_BASE_URL, ADMIN_USER_IDS, or POSTGRES_PASSWORD."
    fill_generated_secrets "$APP_DIR/.env"
  else
    if [[ -f "$APP_DIR/.env" ]]; then
      local stamp backup
      stamp=$(date -u +%Y%m%dT%H%M%SZ)
      backup="$APP_DIR/.env.bak.${stamp}"
      cp "$APP_DIR/.env" "$backup"
      chmod 600 "$backup"
      info "Backed up the previous .env to $backup"
      if docker volume inspect telegram-community-bot_pgdata >/dev/null 2>&1; then
        KEEP_DB_PASSWORD=1
        POSTGRES_PASSWORD=$(env_value POSTGRES_PASSWORD "$backup")
        info "The existing database volume was found. Its password will be kept."
      fi
    fi
    if [[ "$NONINTERACTIVE" -eq 0 ]]; then
      ask_settings
    fi
    if [[ "${KEEP_DB_PASSWORD:-0}" -eq 1 && -n "${backup:-}" ]]; then
      POSTGRES_PASSWORD=$(env_value POSTGRES_PASSWORD "$backup")
    fi
    write_env "$APP_DIR/.env"
  fi

  write_caddy "$WEBHOOK_HOST" "$ADMIN_HOST" "$ADMIN_MODE" "${ACME_EMAIL:-}"
  cat > "$APP_DIR/.install-meta" <<EOF
COMPOSE_PROFILES=$([[ "$WEB_ADMIN_ENABLED" == "true" ]] && echo web-admin || true)
WEBHOOK_HOST=${WEBHOOK_HOST}
ADMIN_HOST=${ADMIN_HOST}
WEBHOOK_BASE_URL=${WEBHOOK_BASE_URL}
ADMIN_PANEL_URL=${ADMIN_PANEL_URL}
BACKUP_DIR=${BACKUP_DIR}
BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-7}
ACME_EMAIL=${ACME_EMAIL:-}
EOF
  chmod 600 "$APP_DIR/.install-meta"
  printf 'BACKUP_RETENTION_DAYS=%s\n' "${BACKUP_RETENTION_DAYS:-7}" > "$APP_DIR/backup.conf"
}

start_stack() {
  cd "$APP_DIR"
  if [[ "$WEB_ADMIN_ENABLED" == "true" ]]; then
    export COMPOSE_PROFILES=web-admin
  else
    unset COMPOSE_PROFILES || true
  fi
  docker compose build
  docker compose up -d postgres redis
  info "Waiting for PostgreSQL"
  local i
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    docker compose exec -T postgres pg_isready -U tg_bot -d tg_bot && break
    sleep 5
  done
  docker compose run --rm --no-deps app python -m database.apply_migrations
  docker compose up -d
}

install_management() {
  install -m 0755 "$APP_DIR/botctl" /usr/local/bin/botctl
  chmod 0755 "$APP_DIR"/scripts/*.sh "$APP_DIR/botctl" "$APP_DIR/setup.sh" || true
  install -m 0644 "$APP_DIR/deploy/telegram-bot-backup.service" /etc/systemd/system/telegram-bot-backup.service
  install -m 0644 "$APP_DIR/deploy/telegram-bot-backup.timer" /etc/systemd/system/telegram-bot-backup.timer
  systemctl daemon-reload
  systemctl enable --now telegram-bot-backup.timer
}

write_summary_file() {
  cat > "$APP_DIR/DEPLOYMENT.txt" <<EOF
Installed: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Webhook host: ${WEBHOOK_BASE_URL}
Admin panel: ${ADMIN_PANEL_URL:-disabled}
Health: ${WEBHOOK_BASE_URL}/health
Bot username: @${BOT_USERNAME}
Backup directory: ${BACKUP_DIR}
Backup retention days: ${BACKUP_RETENTION_DAYS:-7}
Secrets are stored only in ${APP_DIR}/.env (mode 600).
EOF
  chmod 600 "$APP_DIR/DEPLOYMENT.txt"
}

print_summary() {
  local healthy="${1:-0}"
  info
  if [[ "$healthy" -eq 1 ]]; then
    info "Installation complete"
  else
    info "Setup did not finish cleanly"
  fi
  info
  if [[ "$WEB_ADMIN_ENABLED" == "true" ]]; then
    info "Admin panel:"
    info "  ${ADMIN_PANEL_URL}"
    info
    info "Configure this domain for Telegram Login Widget:"
    info
    info "  ${ADMIN_HOST}"
    info
    info "In BotFather run /setdomain and enter that hostname. The site must use HTTPS."
    info
  fi
  info "Health check:"
  info "  ${WEBHOOK_BASE_URL}/health"
  info
  info "Webhook:"
  info "  ${WEBHOOK_BASE_URL}/tg/webhook/<secret hidden>"
  info
  if [[ "$healthy" -eq 1 ]]; then
    info "Services:"
    info "  - app: healthy"
    info "  - PostgreSQL: healthy"
    info "  - Redis: healthy"
    info "  - workers: healthy"
    info "  - HTTPS: active"
    info "  - Telegram webhook: registered"
    info
  fi
  info "Caddy renews the HTTPS certificates automatically."
  info "Backups: ${BACKUP_DIR} (daily, ${BACKUP_RETENTION_DAYS:-7} days)"
  info
  info "Commands:"
  info "  sudo botctl status"
  info "  sudo botctl logs"
  info "  sudo botctl restart"
  info "  sudo botctl update"
  info "  sudo botctl backup"
  info "  sudo botctl migrate"
  info "  sudo botctl shell"
  info "  sudo botctl uninstall"
  info
  info "A copy of this summary without secrets is in ${APP_DIR}/DEPLOYMENT.txt"
}

main() {
  require_root
  require_ubuntu
  install_base_tools
  prepare_config
  check_dns
  install_docker
  configure_firewall
  start_stack
  install_management
  write_summary_file
  info "Waiting for HTTPS and the Telegram webhook"
  local i
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if APP_DIR="$APP_DIR" sh "$APP_DIR/scripts/health-check.sh"; then
      print_summary 1
      exit 0
    fi
    sleep 10
  done
  print_summary 0
  die "Containers were started, but health checks did not pass. Run: sudo botctl logs"
}

main
