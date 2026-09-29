"""Environment-based application settings (Pydantic Settings)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Core
    bot_token: SecretStr = Field(..., description="Telegram Bot API token")
    webhook_base_url: str = Field(
        ...,
        description="Public HTTPS base URL, e.g. https://bot.example.com",
    )
    webhook_path: str = Field("/tg/webhook/{secret}", description="Path template; {secret} replaced")
    webhook_secret: SecretStr = Field(..., description="Secret token segment for webhook URL")

    # Optional Telegram webhook secret header (setWebhook secret_token)
    telegram_webhook_secret_token: SecretStr | None = Field(
        default=None,
        description="If set, Telegram sends X-Telegram-Bot-Api-Secret-Token header",
    )

    # Call setWebhook on startup (disable for local dev without public URL)
    webhook_register_on_startup: bool = Field(
        default=True,
        validation_alias="WEBHOOK_REGISTER_ON_STARTUP",
    )

    # Admins receive user forwards in private chat (comma-separated user ids). Listed as str so
    # pydantic-settings does not JSON-decode env values before validators run.
    admin_user_ids_csv: str = Field(
        ...,
        validation_alias="ADMIN_USER_IDS",
        description="Comma-separated Telegram user ids",
    )

    @field_validator("admin_user_ids_csv")
    @classmethod
    def validate_admin_user_ids_csv(cls, v: str) -> str:
        parts = [p.strip() for p in v.split(",") if p.strip()]
        if not parts:
            raise ValueError("ADMIN_USER_IDS must contain at least one numeric user id")
        for p in parts:
            int(p)
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def admin_user_ids(self) -> list[int]:
        parts = [p.strip() for p in self.admin_user_ids_csv.split(",") if p.strip()]
        return [int(p) for p in parts]

    # Bootstrap: optional first owner user id (seeded on startup if DB has no owners)
    initial_owner_id: int | None = Field(
        default=None,
        description="Telegram user id granted owner role when admins table is empty",
    )

    # Database
    postgres_dsn: str = Field(
        "postgresql://tg_bot:tg_bot@localhost:5432/tg_bot",
        description="asyncpg DSN",
    )
    postgres_pool_min: int = 2
    postgres_pool_max: int = 20

    # Redis
    redis_url: str = Field("redis://localhost:6379/0")
    redis_broadcast_queue: str = "broadcast:jobs"
    redis_scheduler_queue: str = "scheduler:jobs"
    redis_fsm_prefix: str = "fsm:"
    redis_rate_prefix: str = "rate:"
    redis_livestream_prefix: str = "livestream:"
    redis_retention_zset: str = Field(
        "retention:due",
        validation_alias="REDIS_RETENTION_ZSET",
        description="Redis ZSET for leave/retention drip (unique per bot if Redis is shared)",
    )

    # When false, this process only serves HTTP. Worker containers run the loops.
    run_embedded_workers: bool = Field(default=True, validation_alias="RUN_EMBEDDED_WORKERS")

    # Workers
    broadcast_concurrency: int = Field(25, ge=1, le=100)
    broadcast_chunk_size: int = Field(500, ge=1, le=5000)
    scheduler_tick_seconds: float = Field(2.0, ge=0.5)
    retention_tick_seconds: float = Field(5.0, ge=1.0)

    # Rate limits (anti-spam)
    user_message_rate_per_minute: int = Field(30, ge=1)
    admin_reply_rate_per_minute: int = Field(120, ge=1)
    livestream_cooldown_seconds: int = Field(300, ge=0)

    # HTTP server (webhook receiver)
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Storage for downloaded media cache (optional)
    storage_dir: str = "./storage"

    # Post-/start onboarding drip (+1h/+1d/+3d jobs in Postgres); disable to skip scheduling/sends
    onboarding_drip_enabled: bool = Field(default=True, validation_alias="ONBOARDING_DRIP_ENABLED")

    # Optional path to append-only log file for “download full log” in admin panel
    log_file_path: str | None = Field(default=None, validation_alias="LOG_FILE_PATH")

    # Structured logs. Leave false to keep the existing pipe-separated format.
    log_json: bool = Field(default=False, validation_alias="LOG_JSON")

    # Optional Sentry DSN. Ignored when empty. Never returned by the API.
    sentry_dsn: str | None = Field(default=None, validation_alias="SENTRY_DSN")

    # Web admin panel (separate HTTPS domain). Disabled until explicitly turned on.
    web_admin_enabled: bool = Field(default=False, validation_alias="WEB_ADMIN_ENABLED")
    admin_panel_url: str = Field(default="", validation_alias="ADMIN_PANEL_URL")
    telegram_login_bot_username: str = Field(default="", validation_alias="TELEGRAM_LOGIN_BOT_USERNAME")
    web_session_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="WEB_SESSION_SECRET",
    )
    web_session_expire_hours: int = Field(default=24, ge=1, le=168, validation_alias="WEB_SESSION_EXPIRE_HOURS")
    web_cookie_secure: bool = Field(default=True, validation_alias="WEB_COOKIE_SECURE")
    web_cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax",
        validation_alias="WEB_COOKIE_SAMESITE",
    )
    telegram_auth_max_age_seconds: int = Field(
        default=86400,
        ge=60,
        validation_alias="TELEGRAM_AUTH_MAX_AGE_SECONDS",
    )
    web_owner_pin: SecretStr | None = Field(default=None, validation_alias="WEB_OWNER_PIN")
    web_admin_ip_allowlist: str = Field(default="", validation_alias="WEB_ADMIN_IP_ALLOWLIST")
    metrics_token: SecretStr | None = Field(default=None, validation_alias="METRICS_TOKEN")

    webhook_max_body_bytes: int = Field(default=2_000_000, ge=1024, validation_alias="WEBHOOK_MAX_BODY_BYTES")
    webhook_require_secret_token: bool = Field(
        default=False,
        validation_alias="WEBHOOK_REQUIRE_SECRET_TOKEN",
    )
    config_cache_ttl_seconds: int = Field(default=30, ge=1, le=300, validation_alias="CONFIG_CACHE_TTL_SECONDS")
    redis_stream_prefix: str = Field(default="jobs:", validation_alias="REDIS_STREAM_PREFIX")

    @field_validator("admin_panel_url")
    @classmethod
    def strip_panel_slash(cls, v: str) -> str:
        return (v or "").rstrip("/")

    @field_validator("telegram_login_bot_username")
    @classmethod
    def strip_bot_at(cls, v: str) -> str:
        return (v or "").strip().lstrip("@")

    @model_validator(mode="after")
    def validate_web_admin(self) -> "Settings":
        if not self.web_admin_enabled:
            return self
        secret = self.web_session_secret.get_secret_value()
        if len(secret) < 32:
            raise ValueError("WEB_SESSION_SECRET must be at least 32 characters when WEB_ADMIN_ENABLED=true")
        if not self.telegram_login_bot_username:
            raise ValueError("TELEGRAM_LOGIN_BOT_USERNAME is required when WEB_ADMIN_ENABLED=true")
        if not self.admin_panel_url:
            raise ValueError("ADMIN_PANEL_URL is required when WEB_ADMIN_ENABLED=true")
        if self.web_cookie_secure and not self.admin_panel_url.startswith("https://"):
            raise ValueError("ADMIN_PANEL_URL must use https when WEB_COOKIE_SECURE=true")
        return self

    def ip_allowlist(self) -> set[str]:
        return {p.strip() for p in self.web_admin_ip_allowlist.split(",") if p.strip()}

    @field_validator("webhook_base_url")
    @classmethod
    def strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    def webhook_full_url(self) -> str:
        """Full webhook URL registered with Telegram."""
        secret = self.webhook_secret.get_secret_value()
        path = self.webhook_path.format(secret=secret)
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.webhook_base_url}{path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton for process lifetime."""
    return Settings()
