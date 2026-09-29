-- Infrastructure only. Feature tables arrive with their tested vertical slices.
CREATE TABLE bot_installations (
    bot_key VARCHAR(64) PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bot_key_format CHECK (bot_key ~ '^[a-zA-Z0-9_-]{1,64}$')
);

CREATE TABLE update_inbox (
    bot_key VARCHAR(64) NOT NULL REFERENCES bot_installations(bot_key),
    update_id BIGINT NOT NULL CHECK (update_id >= 0),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    state VARCHAR(16) NOT NULL DEFAULT 'RECEIVED'
        CHECK (state IN ('RECEIVED', 'PROCESSED', 'UNSUPPORTED', 'FAILED')),
    PRIMARY KEY (bot_key, update_id)
);
CREATE INDEX inbox_received_idx ON update_inbox(received_at);

CREATE TABLE event_outbox (
    id UUID PRIMARY KEY,
    bot_key VARCHAR(64) NOT NULL REFERENCES bot_installations(bot_key),
    event_key VARCHAR(256) NOT NULL,
    routing_key VARCHAR(32) NOT NULL CHECK (routing_key IN
        ('join', 'welcome', 'delivery.high', 'delivery.normal', 'delivery.low', 'live', 'broadcast', 'retry')),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    due_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    state VARCHAR(16) NOT NULL DEFAULT 'PENDING'
        CHECK (state IN ('PENDING', 'LEASED', 'PUBLISHED', 'DEAD')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    last_error_category VARCHAR(64),
    UNIQUE (bot_key, event_key),
    CONSTRAINT outbox_lease_consistent CHECK (
        (state = 'LEASED' AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
        OR (state <> 'LEASED' AND lease_token IS NULL AND lease_until IS NULL)),
    CONSTRAINT outbox_published_consistent CHECK ((state = 'PUBLISHED') = (published_at IS NOT NULL))
);
CREATE INDEX outbox_due_idx ON event_outbox(due_at, id) WHERE state = 'PENDING';
CREATE INDEX outbox_lease_idx ON event_outbox(lease_until, id) WHERE state = 'LEASED';

CREATE TABLE consumer_receipts (
    consumer_name VARCHAR(64) NOT NULL,
    event_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer_name, event_id)
);
CREATE INDEX consumer_receipts_processed_idx ON consumer_receipts(processed_at);

-- No FK to outbox: receipt retention and producer archival have different lifetimes.
