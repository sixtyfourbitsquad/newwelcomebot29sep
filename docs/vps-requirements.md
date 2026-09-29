# Telegram Welcome & Automation Bot — VPS Production Build

Build this application specifically for **production deployment on a Linux VPS**.

The system must be fast, reliable, resource-efficient, restart-safe, easy to back up, and simple to upgrade without requiring Kubernetes.

## Production Stack

Use:

- **Java 21 LTS**
- **Spring Boot 3.x**
- **PostgreSQL 17+** — permanent/source-of-truth database
- **Redis** — caching, locks, temporary state, deduplication and rate-limit coordination
- **RabbitMQ** — durable background jobs and message queues
- **HikariCP** — PostgreSQL connection pooling
- **Flyway** — database migrations
- **WebClient** — Telegram API communication
- **Resilience4j** — controlled retries/circuit breaking where appropriate
- **Docker**
- **Docker Compose**
- **Caddy or Nginx** — HTTPS reverse proxy
- **Let's Encrypt TLS**
- **Prometheus-compatible metrics**
- **Structured application logging**

Do NOT require Kubernetes for the initial deployment.

The application must run properly on a single VPS while retaining an architecture that can be distributed across multiple servers later.

---

# VPS Architecture

Use this architecture:

Internet / Telegram
        │
        ▼
   HTTPS :443
        │
        ▼
 Caddy / Nginx
        │
        ▼
 Spring Boot API
   / Telegram Webhook
        │
        ├──────────────┐
        ▼              ▼
   PostgreSQL        RabbitMQ
        │              │
        ▼              ├── Join Jobs
      Redis            ├── Welcome Jobs
                       ├── Live Jobs
                       ├── Delivery Jobs
                       └── Broadcast Jobs
                              │
                              ▼
                         Worker Service
                              │
                              ▼
                       Telegram Bot API

The webhook/API process must remain responsive even during very large broadcasts.

---

# Docker Compose

Provide a production-ready `docker-compose.yml`.

Services:

- `app`
- `worker`
- `postgres`
- `redis`
- `rabbitmq`
- `caddy` or `nginx`

Optionally:

- `prometheus`
- `grafana`

Use Docker health checks.

Configure:

`restart: unless-stopped`

for production services where appropriate.

Use named volumes for persistent services.

Example persistent volumes:

- PostgreSQL data
- RabbitMQ data
- Redis data if persistence is enabled
- Caddy certificates/data
- Application logs where required

Application containers must remain stateless.

---

# Resource Management

Because everything initially runs on one VPS, resource usage matters.

Set appropriate Docker CPU/memory limits where practical.

Do NOT allow:

- JVM to consume all VPS RAM
- PostgreSQL to consume uncontrolled memory
- Redis unlimited memory growth
- RabbitMQ uncontrolled queue growth
- Workers to create unlimited threads

Configure JVM container awareness.

Use sensible JVM settings based on available container memory.

The system should fail gracefully under load instead of causing the entire VPS to run out of memory.

---

# Worker Separation

Even on one VPS, run API and workers as separate containers/processes.

Example:

`telegram-api`

Handles:

- Webhooks
- Admin interactions
- Dashboard
- Lightweight requests

`telegram-worker`

Handles:

- Welcome messages
- Join processing
- Live notifications
- Telegram deliveries
- Background jobs

`broadcast-worker`

Handles:

- Large broadcasts
- Batch recipient generation
- Bulk delivery

This separation is critical.

A huge broadcast must NOT make normal bot commands lag.

---

# PostgreSQL

PostgreSQL is the authoritative database.

Persist:

- Users
- Channels
- Admins
- Join requests
- Welcome sequences
- Messages
- Media
- Buttons
- Live settings
- Live events
- Broadcast campaigns
- Deliveries
- Scheduled jobs
- Templates
- Audit logs

Use proper:

- Indexes
- Foreign keys
- Unique constraints
- Transactions
- Connection pooling
- Query optimization
- Keyset pagination

Never load an entire large user database into JVM memory.

---

# PostgreSQL Backups

Production deployment MUST include automatic database backups.

Create a backup script using PostgreSQL-native backup tools.

Example policy:

Daily backup  
→ retain 7 daily backups

Weekly backup  
→ retain 4 weekly backups

Support optional upload to external object storage.

Backups should NOT exist only on the same VPS in a serious production deployment.

Document the complete restore procedure.

A backup that has never been restore-tested should not be treated as sufficient disaster recovery.

---

# Redis

Use Redis only for appropriate temporary/distributed state:

- Cache
- Distributed locks
- Admin FSM/session state
- Deduplication
- Rate-limit coordination
- Temporary live state

PostgreSQL remains the source of truth.

Configure a Redis memory limit and eviction policy appropriate to the selected data.

Critical data must not disappear permanently because Redis restarted.

---

# RabbitMQ

Use durable queues and persistent messages for important asynchronous operations.

Configure:

- Durable queues
- Consumer acknowledgements
- Publisher confirms where appropriate
- Retry queues
- Dead-letter queues
- Exponential backoff
- Poison-message handling

Persist RabbitMQ data using a Docker volume.

Do not acknowledge a job before the critical work has safely completed.

---

# Telegram Webhooks

Production must use Telegram webhooks over HTTPS.

Do NOT use long polling for the production VPS deployment.

Configure:

`https://bot.example.com/telegram/webhook/<secret>`

Use a strong unpredictable webhook path/secret and Telegram-supported webhook security mechanisms where appropriate.

Caddy/Nginx terminates HTTPS and forwards only the required traffic to Spring Boot.

Do NOT expose the Spring Boot application port publicly unless necessary.

---

# Firewall

The VPS setup guide must configure a firewall.

Publicly expose only what is required, normally:

- `22` — SSH, preferably restricted/hardened
- `80` — HTTP redirect/certificate issuance as needed
- `443` — HTTPS

Do NOT publicly expose:

- PostgreSQL `5432`
- Redis `6379`
- RabbitMQ AMQP `5672`
- RabbitMQ management interface
- Internal Spring Boot ports
- Prometheus/Grafana unless deliberately secured

Use Docker internal networks.

---

# Secrets

Store secrets outside source code.

Required configuration may include:

`TELEGRAM_BOT_TOKEN`

`TELEGRAM_WEBHOOK_SECRET`

`POSTGRES_DB`

`POSTGRES_USER`

`POSTGRES_PASSWORD`

`REDIS_PASSWORD`

`RABBITMQ_USER`

`RABBITMQ_PASSWORD`

`APP_DOMAIN`

Never commit `.env` containing real production credentials.

Provide `.env.example`.

---

# Server Restart Recovery

The bot must recover automatically after:

- VPS reboot
- Docker restart
- Application crash
- Worker crash
- Redis restart
- RabbitMQ reconnect
- Temporary PostgreSQL interruption

After reboot:

Docker
   ↓
PostgreSQL / Redis / RabbitMQ
   ↓
API
   ↓
Workers
   ↓
Bot resumes processing

Scheduled welcome messages and broadcasts must NOT disappear because the JVM restarted.

---

# Graceful Shutdown

When deploying a new application version:

1. Stop accepting new worker jobs where appropriate.
2. Finish or safely return in-flight jobs.
3. Close database connections.
4. Close RabbitMQ connections.
5. Shut down cleanly.

A deployment should not randomly lose Telegram deliveries.

---

# Logging

Implement structured logs.

Use Docker log rotation or an equivalent production logging strategy.

Do NOT allow log files to fill the VPS disk.

Log:

- Errors
- Telegram API failures
- Queue failures
- Worker crashes
- Admin actions
- Delivery failures
- Database problems

Never log passwords or bot tokens.

---

# Monitoring

Provide health endpoints and metrics for:

- API status
- PostgreSQL
- Redis
- RabbitMQ
- Queue depth
- Worker status
- Telegram failures
- JVM memory
- CPU
- Database connection pool
- Disk-sensitive conditions where possible

Optionally deploy Prometheus + Grafana.

The bot should be diagnosable without SSHing into the VPS and guessing what went wrong.

---

# Disk Protection

Monitor disk usage.

PostgreSQL data, RabbitMQ queues, backups and logs can consume significant disk space.

Implement:

- Log rotation
- Backup retention
- Queue limits/alerts
- Cleanup jobs

Never allow temporary data or old logs to silently fill the VPS disk.

---

# Performance Principle

Large operations must be asynchronous.

For example:

500,000-recipient Broadcast
          │
          ▼
       RabbitMQ
          │
     Batch Processing
          │
   Telegram Rate Limiter
          │
          ▼
     Telegram API

Meanwhile:

Admin → `/start`
          │
          ▼
      API Container
          │
          ▼
    Immediate Response

Broadcast load must not block admin interaction.

---

# Initial VPS Recommendation

Design the application so it can start comfortably on a sensible production VPS rather than requiring an oversized server from day one.

A practical starting target is approximately:

**4 vCPU**
**8 GB RAM**
**80–160 GB SSD/NVMe**

Actual requirements must be determined by load testing and real usage.

Do not claim that a particular VPS size supports a guaranteed number of users without benchmarks.

For very small initial traffic, a smaller VPS may work, but database + Redis + RabbitMQ + JVM + monitoring all need memory headroom.

---

# Scaling Path

The architecture must allow future migration from:

ONE VPS:

API + Workers + PostgreSQL + Redis + RabbitMQ

to:

Server 1 → Reverse Proxy + API  
Server 2 → Workers  
Server 3 → PostgreSQL  
Server 4 → Redis/RabbitMQ  
Server 5+ → Additional workers

without rewriting the business logic.

Do not introduce Kubernetes until infrastructure scale actually justifies it.

---

# Deployment Commands

Provide scripts/documentation so deployment is simple.

Expected workflow should resemble:

`git pull`

`docker compose build`

`docker compose up -d`

`docker compose ps`

Also provide:

- First installation guide
- Domain/DNS setup
- HTTPS setup
- Telegram webhook registration
- Database migration procedure
- Backup procedure
- Restore procedure
- Upgrade procedure
- Rollback procedure
- Log viewing
- Health checking
- Common troubleshooting

---

# Final Requirement

Treat the VPS as production infrastructure.

Optimize for:

**Fast webhook response + durable queues + PostgreSQL correctness + isolated workers + Telegram-aware rate limiting + restart recovery + backups + monitoring.**

Do not over-engineer the deployment with Kubernetes.

Do not under-engineer the application as a simple bot script.

The target is a bot that is simple to operate on one VPS today but has a core architecture strong enough to scale later.