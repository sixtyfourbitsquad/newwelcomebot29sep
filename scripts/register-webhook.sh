#!/usr/bin/env bash
set -euo pipefail
# Run with exported secrets. Never enable shell tracing.
: "${APP_DOMAIN:?}" "${TELEGRAM_BOT_TOKEN:?}" "${TELEGRAM_WEBHOOK_SECRET:?}"
python3 - <<'PY'
import json, os, urllib.request
payload = dict(url='https://' + os.environ['APP_DOMAIN'] + '/telegram/webhook/' + os.environ['TELEGRAM_WEBHOOK_SECRET'], secret_token=os.environ['TELEGRAM_WEBHOOK_SECRET'], allowed_updates=['message', 'chat_join_request'], drop_pending_updates=False)
request = urllib.request.Request('https://api.telegram.org/bot' + os.environ['TELEGRAM_BOT_TOKEN'] + '/setWebhook', data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        result=json.load(response)
    if not result.get('ok'): raise RuntimeError()
except Exception:
    raise SystemExit('Webhook registration failed; check credentials and domain. Secrets suppressed.')
print('Webhook registered.')
PY
