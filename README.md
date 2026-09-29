# Telegram Welcome & Automation Bot

Java 21 / Spring Boot 3.5 application for a single Linux VPS. API, normal workers,
and broadcast workers run separately. PostgreSQL owns every accepted update and
scheduled job; RabbitMQ carries durable execution signals; Redis coordinates send
rates. Caddy supplies HTTPS. No Kubernetes required.

## Run

Read [the operations guide](docs/operations.md) before exposing the service.

```sh
cp .env.example .env
chmod 600 .env
# Fill in credentials, domain and email.
docker compose build
docker compose up -d
docker compose ps
```

Implemented: authenticated webhooks, duplicate update protection, private `/start`
and `/stop`, configured-channel join approval or temporary welcome sequences,
observed video-chat notifications, scheduled text broadcasts, pause/resume/cancel,
keyset audience generation, bounded queues and retries, an authenticated operator
API and local dashboard, structured logs, metrics, backups and retention.

The initial operator interface is installation-wide with one API key. It does not
implement the proposed multi-admin/channel permission model or a Telegram button
editor. Message/media/button/template tables reserve the schema for those editors;
the current delivery flows send plain text. Broadcasts target users who explicitly
started this bot and have not stopped it; channel membership alone is not permission
to message someone. Welcome steps use the temporary join-request contact window,
are capped at 240 seconds, and are skipped after expiry. Auto approval takes priority
and skips temporary welcome messages. Live events are limited to observed Telegram
`video_chat_started` messages, not universal livestream discovery.

## Reliability contract

* A webhook succeeds only after its inbox and job transaction commits. Redis or
  RabbitMQ downtime cannot lose an accepted update.
* PostgreSQL jobs are scheduled durably. Workers publish UUID signals with confirms
  and poll unclaimed jobs again after 30 seconds. Duplicate signals are harmless.
* Consumers claim with an atomic state transition and acknowledge after recording
  the outcome. Domain effects are transactional and keyed for deduplication.
* Retries use PostgreSQL `due_at`, capped exponential backoff and a maximum of eight
  attempts for unexpected processing errors. Telegram 429 uses `retry_after` plus a
  shared cooldown; rate deferrals do not expire a job.
* Queues and DLQs are durable quorum queues with length/byte bounds and rejection
  on overflow. PostgreSQL keeps work while the broker is full. Single-node quorum
  queues are durable, but do not provide host-level high availability.
* Telegram has no general send idempotency key. A transport timeout or expired send
  lease becomes `UNKNOWN`, not an automatic resend. An operator must reconcile it.
  This trades automatic recovery of uncertain sends for avoiding silent duplicates.
* `COMPLETED` on campaigns means recipient generation completed. Delivery states
  must also be checked; it does not claim every Telegram message succeeded.

## Verification

```sh
mvn test       # unit tests, Java 21 / Maven 3.9+
mvn verify     # additionally requires Docker; Testcontainers PostgreSQL tests
```

The image build runs unit tests. Run `mvn verify` on a Docker-enabled build host
before deployment. See [release validation](docs/validation.md). This repository
does not claim benchmarked capacity or production certification.

Source references: [Telegram Bot API](https://core.telegram.org/bots/api),
[Telegram limits](https://core.telegram.org/bots/faq#broadcasting-to-users),
[Spring Boot 3.5](https://docs.spring.io/spring-boot/3.5/reference/index.html).
