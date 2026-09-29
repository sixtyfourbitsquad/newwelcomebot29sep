# Release validation

Local verification on 2026-09-28: Java 21 compilation and executable JAR packaging
passed; seven unit/configuration tests passed, none skipped. Bash syntax checks
passed for backup, restore, webhook registration and database initialization.
Docker is unavailable on this workstation, so Testcontainers, Compose startup,
Caddy runtime validation, live Telegram checks, load tests and restore drills have
not been run here. CI includes the first three infrastructure checks.

Run `mvn verify` on a Java 21 Docker-enabled host. Unit tests cover webhook secrets
and request limits. Testcontainers tests cover PostgreSQL migrations, transaction
rollback, scheduled-job deduplication and bounded resumable audience generation.

Before a production launch, also execute and record these operational tests:

1. Submit the same webhook concurrently; verify one inbox row and one logical job.
2. Stop RabbitMQ, submit updates, restart it; verify persisted work drains.
3. Stop Redis during sends; verify sending pauses and no uncapped flood occurs.
4. Kill a worker during domain processing and during a Telegram call; verify domain
   recovery and UNKNOWN handling for ambiguous sends.
5. Use a test bot to verify 429 cooldown, blocked-user responses and join permissions.
6. Run a restore drill in an isolated network. Record duration and data checks.
7. Load 500,000 synthetic opted-in users in an isolated database, with Telegram
   disabled/mocked; measure fanout memory, DB size and API p50/p95/p99 separately.
8. Run concurrent webhook load and broadcast fanout, monitor CPU/heap/Hikari waits,
   oldest pending age and disk. Record machine specs and exact image versions.
9. Verify public ports from an external machine, and confirm logs contain no tokens,
   webhook paths, message text or database credentials.

No load-test results, restore drill or live Telegram test are claimed by this file.
Image tags should be resolved and pinned to reviewed digests in the release process.
