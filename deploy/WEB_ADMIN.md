# Web admin panel

The bot can serve an admin site on a hostname you already own. The domain comes from `ADMIN_PANEL_URL`. Nothing in the app hardcodes `admin.example.com`.

Telegram `/admin` stays. User messages are still forwarded to every admin, and an admin still answers by using Reply on that forwarded message.

## DNS and HTTPS

1. Create an **A** record for the admin hostname, for example `admin.example.com`, pointing at the VPS.
2. Issue a certificate:

```bash
sudo certbot --nginx -d admin.example.com
```

3. Proxy that host to the same uvicorn process as the bot. See the second `server` block in `deploy/nginx.example.conf`.
4. Open TCP 80 and 443. Do not publish PostgreSQL or Redis to the internet.

The webhook host (`WEBHOOK_BASE_URL`) and the admin host can be different names on the same server.

## BotFather

Before login will work:

1. Open [@BotFather](https://t.me/BotFather).
2. `/setdomain`
3. Choose this bot.
4. Enter the admin hostname only, for example `admin.example.com`.

The site must be HTTPS. The widget username is `TELEGRAM_LOGIN_BOT_USERNAME` (without `@`).

## Environment

```env
WEB_ADMIN_ENABLED=true
ADMIN_PANEL_URL=https://admin.example.com
TELEGRAM_LOGIN_BOT_USERNAME=your_bot_username
WEB_SESSION_SECRET=replace-with-32-or-more-random-characters
WEB_SESSION_EXPIRE_HOURS=24
WEB_COOKIE_SECURE=true
WEB_COOKIE_SAMESITE=lax
TELEGRAM_WEBHOOK_SECRET_TOKEN=another-long-random-string
WEBHOOK_REQUIRE_SECRET_TOKEN=true
```

Optional:

| Variable | Purpose |
|---|---|
| `WEB_OWNER_PIN` | Extra PIN after Telegram login for owner accounts |
| `WEB_ADMIN_IP_ALLOWLIST` | Comma-separated IPs. Empty disables the check. This is not the main login control |
| `TELEGRAM_AUTH_MAX_AGE_SECONDS` | Reject old widget payloads. Default 86400 |
| `METRICS_TOKEN` | Require `Authorization: Bearer ...` on `/metrics` |
| `SENTRY_DSN` | Error reporting when `sentry-sdk` is installed |
| `LOG_JSON` | Write logs as JSON lines |
| `CONFIG_CACHE_TTL_SECONDS` | Short cache for configuration reads |

`WEB_SESSION_SECRET`, `BOT_TOKEN`, `POSTGRES_DSN`, `REDIS_URL`, and webhook secrets are never returned to the browser.

## Database migration

On an existing database, from the project root:

```bash
python -m database.apply_migrations
```

That applies `database/migrations/004_web_admin.sql` and records it in `schema_migrations`. It adds columns and tables. It does not delete users, broadcasts, or messages.

A brand-new database can use `database/schema.sql` instead. Do not run both on a new database and then also expect the migration runner to reapply `004` if those tables were already created by `schema.sql`; `004` is written to be safe to re-run, and the runner skips a file only after it has recorded the filename.

Restart the service after migrating.

## Processes

One uvicorn process still runs the webhook, the admin site, and the background workers:

- broadcast worker
- scheduler worker
- onboarding worker
- retention worker

They are separate asyncio tasks with separate responsibilities. Broadcasts are queued in Redis and are not sent inside the webhook request. The webhook checks the secret header, drops duplicate update ids, stores the update id, and returns.

Redis Streams helpers live in `services/job_stream.py` for new reliable jobs (ack, retry, dead letter). The existing broadcast, scheduler, retention, and onboarding workers keep their current queues so a restart does not send those jobs twice or drop jobs already waiting.

Redis is also used for locks, rate limits, login-widget cooldowns, and a short configuration cache. PostgreSQL remains the record of users, sessions, inbox rows, and audit events.

## Cookies

The session cookie `wa_session` is HTTP-only. In production `WEB_COOKIE_SECURE=true` and `SameSite=lax`. Logging out revokes the server session. Sessions expire after `WEB_SESSION_EXPIRE_HOURS`.

State-changing requests must send the `X-CSRF-Token` header returned by login and by `/panel/api/me`.

## Backups

Dump each bot database on its own schedule:

```bash
pg_dump "postgresql://tg_bot:PASSWORD@127.0.0.1:5432/tg_bot" -Fc -f /var/backups/tg_bot.dump
```

Copy Redis only if you need in-flight queues. The permanent data is in PostgreSQL.

## Checks

```bash
curl -sS https://admin.example.com/health
```

Open `https://admin.example.com`, sign in with Telegram, and confirm `/admin` in the bot still opens the Telegram panel.
