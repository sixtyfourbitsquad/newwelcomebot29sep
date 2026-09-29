# Linux VPS operations

## First installation

Use a supported Linux distribution with Docker Engine and the Compose plugin.
Start around 4 vCPU, 8 GB RAM, and 80–160 GB SSD, then measure real load. This is a
planning target, not a guaranteed user capacity. The configured service memory
limits leave host headroom on an 8 GB machine. Backups and image builds need extra
disk/RAM; use a separate build machine if necessary.

Install Docker from its official distribution instructions. Enable it on boot:
`sudo systemctl enable --now docker`. Use SSH keys, disable password/root login
after confirming another SSH session works, and restrict SSH at the cloud firewall.

```sh
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from YOUR_ADMIN_IP to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Docker published ports can bypass UFW rules. This Compose file publishes only
80/443 and optionally loopback 9090. Verify actual exposure externally, also use
the provider firewall, and do not add published DB/Redis/Rabbit ports. IPv6 needs
equivalent firewall rules. Backend is an internal Docker network; only application
processes and Caddy also join an egress network for Telegram/ACME access.

Clone/copy this project to `/opt/welcome-bot`. Create `.env` from `.env.example`,
chmod 600, and use unique random credentials (`openssl rand -hex 32`). Do not commit
real secrets or paste `docker compose config` output into tickets. Environment
secrets are visible to host administrators; a secret manager can inject equivalent
environment values. Use passwords without shell interpolation characters if you
plan to source `.env` for the webhook script. `.env` is executable shell input only
when sourced: source only your own trusted configuration.

Point the domain's A record to the VPS; add AAAA only if IPv6 works. Permit inbound
80/443 and outbound DNS/HTTPS. Caddy automatically requests and renews Let's Encrypt
certificates using its persistent data volume. Do not enable proxy access logs that
record the webhook path, which contains a secret.

```sh
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=100 app worker broadcast-worker
set -a; . ./.env; set +a
bash scripts/register-webhook.sh
```

Add the bot to a test channel/group with the invite-user permission for approval.
The registration script preserves pending updates and requests only supported types.
No long polling is used. Test `/start` and `/stop` in a private chat.

## Operator API and dashboard

The reverse proxy intentionally exposes only webhook POSTs. On the VPS find the API
container's backend IP using `docker inspect` and open a local SSH tunnel from your
workstation: `ssh -L 8080:CONTAINER_BACKEND_IP:8080 operator@VPS`. Visit
`http://localhost:8080/admin/dashboard`. It uses the API key in memory; close the tab
when finished. Never send the key over public HTTP. Rotate it by changing the
environment and recreating app containers. This is a trusted single-operator API,
not a multi-tenant dashboard.

All JSON API routes require `Authorization: Bearer ADMIN_API_KEY`:

| Method / route | JSON / behavior |
|---|---|
| PUT `/admin/channels` | `{"id":-100123,"title":"Example","autoApprove":false,"welcome":"Welcome!","liveText":"We are live"}` |
| PUT `/admin/channels/-100123/welcome` | `[{"position":0,"delaySeconds":0,"text":"Welcome!"},{"position":1,"delaySeconds":60,"text":"More information"}]` |
| POST `/admin/broadcasts` | `{"text":"Hello subscribers","delaySeconds":0}`; returns campaign UUID |
| POST `/admin/broadcasts/UUID/pause` | Pause generation and sends |
| POST `/admin/broadcasts/UUID/resume` | Resume from durable cursor |
| POST `/admin/broadcasts/UUID/cancel` | Terminal cancellation; in-flight sends can finish |
| GET `/admin/status` | Job aggregates and newest 50 campaigns |
| GET `/admin/jobs?state=UNKNOWN` | Latest 100 jobs needing reconciliation; `DEAD` also supported |

Configure only channels you administer. This version trusts the operator's channel
IDs; Telegram validates the bot's permissions when executing approval/sends.
Job payload snapshots keep already-scheduled content independent of later edits.

## Backups and restore

Daily PostgreSQL custom-format backups retain seven daily and four Sunday copies.
Install the provided systemd units, adjusting the project path if needed:

```sh
sudo cp deploy/welcome-bot-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now welcome-bot-backup.timer
sudo systemctl start welcome-bot-backup.service
sudo journalctl -u welcome-bot-backup.service
```

Optional `/etc/welcome-bot-backup.env` (root-only, mode 600):
`BACKUP_S3_URI=s3://YOUR_BUCKET/welcome-bot` and optionally `BACKUP_DIR=...`.
Install AWS CLI, use a least-privilege upload role, enable bucket encryption,
versioning and an off-server retention/lifecycle policy. Local retention never
deletes remote files. Verify remote uploads and configure alerts for unit failures;
without this, the default backup is local-only and cannot cover VPS loss.
Back up `.env` securely and separately; never put plaintext secrets in a public bucket.

Restore first into an isolated test deployment with no Telegram connectivity:

1. Retrieve a selected backup and check its date and checksum.
2. Start PostgreSQL only: `docker compose up -d postgres`.
3. Run `bash scripts/restore.sh /absolute/backup.dump --replace-database`.
   This explicitly replaces the database and stops application processes.
4. Inspect counts, Flyway history, campaign cursors and pending/unknown jobs.
5. Reconcile messages sent after the backup timestamp: restoring an old snapshot
   can replay externally delivered messages. Mark those jobs appropriately before
   re-enabling Telegram access. Broker signals do not override database state.
6. Start with the compatible application version: `docker compose up -d`.
7. Verify `/start`, health, backlog recovery, and record the restore drill's results.

`pg_restore --list` validates the archive structure, not recoverability. Perform a
real isolated restore drill monthly and after schema changes. Protect snapshots as
personal data. Redis is reconstructible temporary state, Rabbit queues are execution
signals recoverable from PostgreSQL; their volumes still persist across restarts.

## Upgrade and rollback

Tag and retain the previous application image before upgrades. Build on CI where
possible. Take and verify a backup, run integration checks, then:

```sh
git pull --ff-only
docker compose build
docker compose stop worker broadcast-worker
docker compose up -d app
docker compose ps
# Wait for app health and inspect migration logs.
docker compose up -d worker broadcast-worker
```

Flyway runs only in the API process before it becomes ready. Workers wait for API
health on first startup. Additive migrations must remain compatible during upgrades;
do not deploy destructive schema changes without a planned maintenance window.
On a new database volume, `deploy/init-runtime.sh` creates `automation_runtime`
with table DML and sequence access only. Flyway uses the owner credentials, while
API queries and workers use `RUNTIME_DB_PASSWORD`. The init script runs only on
first initialization; existing volumes require explicitly provisioning that role
and grants, including grants on existing tables/sequences. Changing `.env` does not
rotate an existing PostgreSQL role password; use `ALTER ROLE` securely as well.
Owner credentials remain in the API environment for Flyway. A hardened release
pipeline can instead run migrations in a separate process and remove them from API.

Rollback code by selecting the previous `APP_IMAGE` and running
`docker compose up -d --no-build`. Flyway does not automatically downgrade. If the
old code is schema-incompatible, stop processing and restore the pre-upgrade backup
using the reconciliation procedure above. Never use `docker compose down -v` during
an upgrade; it destroys persistent data.

SIGTERM allows listener shutdown and in-flight work within the 60-second Compose
grace period. A hard crash leaves a lease; domain jobs retry after 90 seconds,
delivery jobs become UNKNOWN. Redis failure fails closed for sends. PostgreSQL
failure makes webhooks non-2xx so Telegram can retry. RabbitMQ failure leaves jobs
pending in PostgreSQL until reconnection.

## Monitoring, disk and troubleshooting

`docker compose --profile monitoring up -d` starts private Prometheus. Tunnel
localhost:9090 over SSH to inspect targets, JVM/Hikari/process metrics, persisted job
counts and queue metrics. Alert rules are supplied; route them through your own
Alertmanager/on-call integration before launch. Prometheus alone does not deliver
notifications. Add a host disk exporter or external VPS monitoring for the actual
PostgreSQL, Docker and backup filesystems; application disk metrics only describe
its container filesystem.

```sh
docker compose ps
docker compose exec app curl -fsS http://localhost:9091/actuator/health/readiness
docker compose logs --since=10m --tail=200 worker
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers
df -h /var/lib/docker /var/backups/welcome-bot
```

Structured stdout logs rotate at 3 x 10 MB per service. Queues reject overflow;
Rabbit stops accepting writes below 2 GB free disk. Redis uses noeviction and 128 MB
max memory so rate-limit keys are not silently evicted. Jobs/inbox retain dedupe
tombstones; completed personal payloads are scrubbed in hourly batches after 30/7
days. Tombstones and audit history still grow: monitor DB size, archive with a
documented replay horizon, and increase/partition storage before exhaustion.

* Unhealthy app: inspect migration errors, database credentials and dependency health.
* Webhook 403: header and path must both exactly match the configured secret.
* No welcome: channel not configured, approval enabled, or temporary contact expired.
* Delivery 403: user blocked the bot; they are deactivated for future broadcasts.
* 429: shared cooldown honors Telegram's `retry_after`; do not add aggressive retries.
* UNKNOWN: reconcile Telegram outcome manually; do not bulk reset these to PENDING.
* Growing pending count: inspect worker health, rate limits, queue overflow and disk.
* TLS problems: check DNS, clock, Caddy logs and ports before repeatedly reissuing certs.

## Scaling

Move PostgreSQL/Redis/RabbitMQ to private remote endpoints via environment variables,
use TLS for cross-host connections, and configure network ACLs. Add worker replicas
without changing durable state or rate coordination. Each installation must have its
own database/Redis namespace and bot token; this build supports one bot per stack.
Increase pools only after database and latency measurements. Separate the broadcast
database workload further if load tests show contention. Preserve shared Redis rate
coordination across all senders. No Kubernetes is required.
