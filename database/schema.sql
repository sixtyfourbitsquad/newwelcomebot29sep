-- PostgreSQL schema for Community Broadcast Bot
-- Run once on empty database (see deploy/POSTGRESQL.md)

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- users: collected subscribers / chatters
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id           BIGINT PRIMARY KEY,
    username          TEXT,
    first_name        TEXT,
    language_code     TEXT,
    join_date         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen         TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    source_channel    TEXT,
    broadcast_status  TEXT NOT NULL DEFAULT 'active',
    total_messages    BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users (last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_users_broadcast_status ON users (broadcast_status);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users (is_active) WHERE is_active = TRUE;

-- ---------------------------------------------------------------------------
-- admins
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admins (
    admin_id      BIGINT PRIMARY KEY,
    role          TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'moderator', 'support')),
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by      BIGINT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    BIGINT,
    last_login_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_admins_role ON admins (role);

-- ---------------------------------------------------------------------------
-- broadcasts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS broadcasts (
    id                BIGSERIAL PRIMARY KEY,
    created_by        BIGINT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'draft',
    payload           JSONB NOT NULL DEFAULT '{}',
    scheduled_at      TIMESTAMPTZ,
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    total_targets     BIGINT NOT NULL DEFAULT 0,
    delivered_count   BIGINT NOT NULL DEFAULT 0,
    failed_count      BIGINT NOT NULL DEFAULT 0,
    blocked_count     BIGINT NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts (status);
CREATE INDEX IF NOT EXISTS idx_broadcasts_created_at ON broadcasts (created_at DESC);

-- ---------------------------------------------------------------------------
-- broadcast_logs (per-recipient outcome)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS broadcast_logs (
    id             BIGSERIAL PRIMARY KEY,
    broadcast_id   BIGINT NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    user_id        BIGINT NOT NULL,
    status         TEXT NOT NULL,
    error_code     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_broadcast_logs_broadcast ON broadcast_logs (broadcast_id);
CREATE INDEX IF NOT EXISTS idx_broadcast_logs_user ON broadcast_logs (user_id);

-- ---------------------------------------------------------------------------
-- scheduled_jobs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id             BIGSERIAL PRIMARY KEY,
    created_by     BIGINT NOT NULL,
    run_at         TIMESTAMPTZ NOT NULL,
    payload        JSONB NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at   TIMESTAMPTZ,
    retry_count    INT NOT NULL DEFAULT 0,
    last_error     TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_run ON scheduled_jobs (run_at) WHERE status = 'pending';

-- ---------------------------------------------------------------------------
-- welcome_messages (multi-step)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS welcome_messages (
    id          BIGSERIAL PRIMARY KEY,
    step_order  INT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    UNIQUE (step_order)
);

-- ---------------------------------------------------------------------------
-- retention_messages
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS retention_messages (
    id              BIGSERIAL PRIMARY KEY,
    step_order      INT NOT NULL,
    delay_seconds   INT NOT NULL DEFAULT 3600,
    payload         JSONB NOT NULL DEFAULT '{}',
    UNIQUE (step_order)
);

-- ---------------------------------------------------------------------------
-- livestream_settings (singleton row id=1)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS livestream_settings (
    id                      SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    cooldown_seconds        INT NOT NULL DEFAULT 300,
    notification_template   TEXT NOT NULL DEFAULT '🔴 LIVE STREAM STARTED! Join now!',
    banner_payload          JSONB,
    button_payload          JSONB,
    manual_live_url         TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO livestream_settings (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- channel_settings (monitored chat + retention toggles)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS channel_settings (
    id                          SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    monitored_chat_id           BIGINT,
    retention_enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    auto_approve_join_requests  BOOLEAN NOT NULL DEFAULT FALSE,
    join_requests_total         BIGINT NOT NULL DEFAULT 0,
    welcome_enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    onboarding_enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO channel_settings (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- inline_buttons (saved keyboard presets)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inline_buttons (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    buttons     JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- system_logs (audit)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_logs (
    id          BIGSERIAL PRIMARY KEY,
    level       TEXT NOT NULL,
    source      TEXT NOT NULL,
    message     TEXT NOT NULL,
    context     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_system_logs_created ON system_logs (created_at DESC);

-- ---------------------------------------------------------------------------
-- user_activity
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_activity (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    action      TEXT NOT NULL,
    meta        JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_activity_user ON user_activity (user_id);
CREATE INDEX IF NOT EXISTS idx_user_activity_created ON user_activity (created_at DESC);

-- ---------------------------------------------------------------------------
-- onboarding_messages + onboarding_drip_jobs (post-/start drip, PG-backed)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_messages (
    step_order      INT PRIMARY KEY CHECK (step_order >= 1 AND step_order <= 20),
    delay_seconds   INT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'
);

INSERT INTO onboarding_messages (step_order, delay_seconds, payload) VALUES
    (1, 3600, '{}'),
    (2, 86400, '{}'),
    (3, 259200, '{}')
ON CONFLICT (step_order) DO NOTHING;

CREATE TABLE IF NOT EXISTS onboarding_drip_jobs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    step_order      INT NOT NULL,
    fire_at         TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    UNIQUE (user_id, step_order)
);

CREATE INDEX IF NOT EXISTS idx_onboarding_drip_fire_pending
    ON onboarding_drip_jobs (fire_at)
    WHERE sent_at IS NULL;

-- ---------------------------------------------------------------------------
-- Web admin sessions, shared inbox, audit, job metadata (see migrations/004)
-- ---------------------------------------------------------------------------
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
CREATE INDEX IF NOT EXISTS idx_admins_active ON admins (admin_id) WHERE is_active = TRUE;
