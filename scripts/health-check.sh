#!/bin/sh
# Check containers, HTTPS, data stores, workers, and the Telegram webhook.
# Exit 1 when any check fails. Does not print tokens or webhook secrets.
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

FAIL=0
ok() { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; FAIL=1; }

echo "Health checks"

for svc in caddy app postgres redis broadcast-worker scheduler-worker onboarding-worker retention-worker; do
  if docker compose ps --status running --services | grep -qx "$svc"; then
    ok "$svc running"
  else
    bad "$svc not running"
  fi
done

if [ "${COMPOSE_PROFILES:-}" = "web-admin" ]; then
  if docker compose ps --status running --services | grep -qx admin; then
    ok "admin running"
  else
    bad "admin not running"
  fi
fi

if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-tg_bot}" -d "${POSTGRES_DB:-tg_bot}" >/dev/null 2>&1; then
  ok "PostgreSQL reachable"
else
  bad "PostgreSQL not reachable"
fi

if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -qx PONG; then
  ok "Redis reachable"
else
  bad "Redis not reachable"
fi

WEBHOOK_BASE_URL="${WEBHOOK_BASE_URL:-}"
ADMIN_PANEL_URL="${ADMIN_PANEL_URL:-}"
WEBHOOK_HOST="${WEBHOOK_HOST:-}"

if [ -n "$WEBHOOK_BASE_URL" ]; then
  if curl -fsS --max-time 20 "$WEBHOOK_BASE_URL/health" | grep -q '"status"'; then
    ok "webhook health endpoint"
  else
    bad "webhook health endpoint"
  fi
fi

if [ -n "$ADMIN_PANEL_URL" ]; then
  case "$ADMIN_PANEL_URL" in
    */admin)
      admin_check="$ADMIN_PANEL_URL"
      ;;
    *)
      admin_check="${ADMIN_PANEL_URL}/health"
      ;;
  esac
  if curl -fsS --max-time 20 "$admin_check" >/dev/null; then
    ok "admin panel reachable"
  else
    bad "admin panel not reachable"
  fi
fi

cert_ok() {
  host="$1"
  timeout 20 sh -c "echo | openssl s_client -connect ${host}:443 -servername ${host} 2>/dev/null | openssl x509 -noout -checkend 0" >/dev/null 2>&1
}

if [ -n "$WEBHOOK_HOST" ]; then
  if cert_ok "$WEBHOOK_HOST"; then
    ok "HTTPS certificate"
  else
    bad "HTTPS certificate"
  fi
fi

if [ -n "${ADMIN_HOST:-}" ] && [ "$ADMIN_HOST" != "$WEBHOOK_HOST" ]; then
  if cert_ok "$ADMIN_HOST"; then
    ok "admin HTTPS certificate"
  else
    bad "admin HTTPS certificate"
  fi
fi

if docker compose exec -T app python - <<'PY'
import json, os, urllib.request
token = os.environ["BOT_TOKEN"]
base = os.environ["WEBHOOK_BASE_URL"].rstrip("/")
expected = os.environ.get("TELEGRAM_LOGIN_BOT_USERNAME", "")

def get(method):
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", timeout=30) as resp:
        body = json.load(resp)
    if not body.get("ok"):
        raise SystemExit("telegram api rejected the token")
    return body["result"]

me = get("getMe")
username = str(me.get("username") or "")
if expected and username.lower() != expected.lower():
    raise SystemExit("bot username does not match TELEGRAM_LOGIN_BOT_USERNAME")
info = get("getWebhookInfo")
url = str(info.get("url") or "")
if not url.startswith(base + "/"):
    raise SystemExit("webhook is not registered on WEBHOOK_BASE_URL")
print("telegram-ok")
PY
then
  ok "Telegram token and webhook"
else
  bad "Telegram token or webhook"
fi

if [ "$FAIL" -ne 0 ]; then
  echo "One or more checks failed. Database volumes were not changed."
  exit 1
fi
echo "All checks passed."
