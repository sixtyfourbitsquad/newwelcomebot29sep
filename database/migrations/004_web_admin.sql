-- Web admin panel, shared-inbox records, audit, and job metadata.
-- Safe to re-run. Does not delete existing rows.

ALTER TABLE admins ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE admins ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE admins ADD COLUMN IF NOT EXISTS updated_by BIGINT;
ALTER TABLE admins ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

ALTER TABLE admins DROP CONSTRAINT IF EXISTS admins_role_check;
ALTER TABLE admins ADD CONSTRAINT admins_role_check
    CHECK (role IN ('owner', 'admin', 'moderator', 'support'));

CREATE INDEX IF NOT EXISTS idx_admins_active ON admins (admin_id) WHERE is_active = TRUE;

ALTER TABLE channel_settings
    ADD COLUMN IF NOT EXISTS welcome_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS onboarding_enabled BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE scheduled_jobs
    ADD COLUMN IF NOT EXISTS retry_count INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_error TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS web_sessions (
    id            BIGSERIAL PRIMARY KEY,
    token_hash    TEXT NOT NULL UNIQUE,
    admin_id      BIGINT NOT NULL,
    role          TEXT NOT NULL,
    csrf_token    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    revoked_at    TIMESTAMPTZ,
    ip            TEXT,
    user_agent    TEXT
);

CREATE INDEX IF NOT EXISTS idx_web_sessions_admin ON web_sessions (admin_id);
CREATE INDEX IF NOT EXISTS idx_web_sessions_expires ON web_sessions (expires_at);

CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id          BIGSERIAL PRIMARY KEY,
    admin_id    BIGINT,
    action      TEXT NOT NULL,
    success     BOOLEAN NOT NULL DEFAULT TRUE,
    ip          TEXT,
    detail      JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_admin ON admin_audit_logs (admin_id);

CREATE TABLE IF NOT EXISTS inbox_messages (
    id                   BIGSERIAL PRIMARY KEY,
    telegram_user_id     BIGINT NOT NULL,
    original_message_id  BIGINT,
    message_type         TEXT NOT NULL,
    content_text         TEXT,
    file_id              TEXT,
    payload              JSONB NOT NULL DEFAULT '{}',
    received_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (telegram_user_id, original_message_id)
);

CREATE INDEX IF NOT EXISTS idx_inbox_messages_user_time
    ON inbox_messages (telegram_user_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_inbox_messages_received
    ON inbox_messages (received_at DESC);

CREATE TABLE IF NOT EXISTS inbox_forwards (
    id                    BIGSERIAL PRIMARY KEY,
    inbox_message_id      BIGINT NOT NULL REFERENCES inbox_messages(id) ON DELETE CASCADE,
    admin_telegram_id     BIGINT NOT NULL,
    forwarded_message_id  BIGINT,
    delivery_status       TEXT NOT NULL,
    telegram_error        TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inbox_forwards_lookup
    ON inbox_forwards (admin_telegram_id, forwarded_message_id);
CREATE INDEX IF NOT EXISTS idx_inbox_forwards_inbox ON inbox_forwards (inbox_message_id);

CREATE TABLE IF NOT EXISTS inbox_replies (
    id                  BIGSERIAL PRIMARY KEY,
    inbox_message_id    BIGINT REFERENCES inbox_messages(id) ON DELETE SET NULL,
    admin_telegram_id   BIGINT NOT NULL,
    target_user_id      BIGINT NOT NULL,
    reply_message_id    BIGINT,
    source_message_id   BIGINT,
    message_type        TEXT NOT NULL,
    content_text        TEXT,
    payload             JSONB NOT NULL DEFAULT '{}',
    delivery_status     TEXT NOT NULL,
    telegram_error      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_inbox_replies_source
    ON inbox_replies (admin_telegram_id, source_message_id)
    WHERE source_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inbox_replies_target
    ON inbox_replies (target_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inbox_replies_status
    ON inbox_replies (delivery_status, created_at DESC);

CREATE TABLE IF NOT EXISTS processed_telegram_updates (
    update_id     BIGINT PRIMARY KEY,
    processed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS job_runs (
    id             BIGSERIAL PRIMARY KEY,
    job_type       TEXT NOT NULL,
    job_key        TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    retry_count    INT NOT NULL DEFAULT 0,
    max_retries    INT NOT NULL DEFAULT 5,
    next_retry_at  TIMESTAMPTZ,
    last_error     TEXT,
    dead_letter    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_type, job_key)
);

CREATE INDEX IF NOT EXISTS idx_job_runs_status_next ON job_runs (status, next_retry_at);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
