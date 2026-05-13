from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MIMIR_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    env: str = "development"
    # auth_mode: dev | single_user | multi_user
    # Legacy aliases: "prod" → multi_user, "" → auto from MIMIR_ENV
    auth_mode: str = ""
    dev_api_key: str = "local-dev-key"  # accepted without DB in dev mode
    secret_key: str = "change-me"
    api_key: str = "local-dev-key"  # legacy single-key field kept for compat

    # ── OAuth / Multi-user ────────────────────────────────────────────────────
    allow_registration: bool = False       # open user registration (off by default)
    require_https: bool = True             # enforce HTTPS for OAuth in multi_user mode
    oauth_enabled: bool = True             # enable OAuth endpoints
    access_token_ttl_seconds: int = 3600   # 1 hour
    refresh_token_ttl_seconds: int = 2592000  # 30 days

    @property
    def _effective_auth_mode(self) -> str:
        """Normalize auth_mode: handle legacy aliases and empty string."""
        if self.auth_mode in ("single_user", "multi_user", "dev"):
            return self.auth_mode
        if self.auth_mode == "prod":
            return "multi_user"
        # empty or unknown → derive from env
        return "dev" if self.env == "development" else "multi_user"

    @property
    def is_dev_auth(self) -> bool:
        return self._effective_auth_mode == "dev"

    @property
    def is_single_user(self) -> bool:
        return self._effective_auth_mode == "single_user"

    @property
    def is_multi_user(self) -> bool:
        return self._effective_auth_mode == "multi_user"

    # ── Data paths ────────────────────────────────────────────────────────────
    data_dir: Path = Path("./data")
    vector_dir: Path = Path("./data/vectors")
    database_url: str = ""          # optional Postgres URL; empty = SQLite in data_dir
    db_pool_size: int = 5           # connection pool size (Postgres only)
    db_max_overflow: int = 10       # max extra connections above pool_size
    db_pool_timeout: int = 30       # seconds to wait for a connection from pool

    host: str = "0.0.0.0"
    port: int = 8787
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:4173"]
    public_url: str = ""            # base URL exposed to the internet (used for deep-links)

    embedding_model: str = "all-MiniLM-L6-v2"
    default_token_budget: int = 2048

    # ── System mutation endpoints ─────────────────────────────────────────────
    # POST /system/consolidate|reflect|lifecycle — off by default.
    # Enable in dev/test via MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true.
    enable_system_mutation_endpoints: bool = False

    # ── Performance limits ────────────────────────────────────────────────────
    max_memories_per_context: int = 20
    max_vector_candidates: int = 50
    max_token_budget: int = 8192
    max_reflections_per_hour: int = 10
    max_improvements_per_day: int = 20
    max_worker_concurrency: int = 3

    # ── Slack ─────────────────────────────────────────────────────────────────
    slack_bot_token: str = Field(default="", alias="SLACK_BOT_TOKEN")
    slack_approval_channel: str = Field(default="#mimir-approvals", alias="SLACK_APPROVAL_CHANNEL")
    slack_signing_secret: str = Field(default="", alias="SLACK_SIGNING_SECRET")

    # ── VAPID (PWA push) ──────────────────────────────────────────────────────
    vapid_private_key: str = Field(default="", alias="VAPID_PRIVATE_KEY")
    vapid_public_key: str = Field(default="", alias="VAPID_PUBLIC_KEY")
    vapid_claim_email: str = Field(default="admin@example.com", alias="VAPID_CLAIM_EMAIL")

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_bot_token)

    @property
    def pwa_push_enabled(self) -> bool:
        return bool(self.vapid_private_key and self.vapid_public_key)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.vector_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


# Insecure defaults that must not appear in production
_PROD_INSECURE_DEFAULTS: dict[str, str] = {
    "secret_key": "change-me",
    "api_key": "local-dev-key",
    "dev_api_key": "local-dev-key",
}

# Required non-empty fields in production
_PROD_REQUIRED: list[tuple[str, str]] = [
    ("secret_key",  "MIMIR_SECRET_KEY — used to sign tokens"),
    ("data_dir",    "MIMIR_DATA_DIR   — path to persistent data directory"),
    ("vector_dir",  "MIMIR_VECTOR_DIR — path to vector index directory"),
]

_VALID_AUTH_MODES = ("dev", "single_user", "multi_user", "prod", "")


def validate_config(settings: Settings) -> None:
    """Validate critical config at startup. Raises SystemExit with a clear message on failure."""
    errors: list[str] = []
    warnings: list[str] = []

    is_prod = settings.env != "development"
    effective_mode = settings._effective_auth_mode

    # auth_mode must be a known value
    if settings.auth_mode not in _VALID_AUTH_MODES:
        errors.append(
            f"  INVALID: MIMIR_AUTH_MODE={settings.auth_mode!r} — must be dev, single_user, or multi_user"
        )

    if is_prod:
        # Required fields must be present and non-empty
        for field, desc in _PROD_REQUIRED:
            val = getattr(settings, field, None)
            if not val:
                errors.append(f"  MISSING: {desc}")
            elif str(val) == _PROD_INSECURE_DEFAULTS.get(field, ""):
                errors.append(
                    f"  INSECURE DEFAULT: {field!r} still set to {str(val)!r} — set {desc}"
                )

        # auth_mode must be explicit in non-development envs
        if not settings.auth_mode:
            errors.append(
                "  MISSING: MIMIR_AUTH_MODE must be 'dev', 'single_user', or 'multi_user' in non-development envs"
            )

        # multi_user mode: extra requirements
        if effective_mode == "multi_user":
            for field in ("api_key", "dev_api_key"):
                val = getattr(settings, field, "")
                if val == _PROD_INSECURE_DEFAULTS.get(field, ""):
                    errors.append(
                        f"  INSECURE DEFAULT: {field!r} is still the default dev key in multi_user mode"
                    )

        # CORS origins: wildcard in prod is a security risk
        if "*" in settings.cors_origins:
            errors.append(
                "  INSECURE: MIMIR_CORS_ORIGINS contains '*' — specify explicit origins in production"
            )

    else:
        # Development: warn about potentially insecure settings, don't fail
        if settings.secret_key == "change-me":
            warnings.append(
                "  [warn] MIMIR_SECRET_KEY is still 'change-me' — fine for dev, change before production"
            )

    # Slack: if bot token is set, signing secret should also be set
    if settings.slack_enabled and not settings.slack_signing_secret:
        msg = "  MISSING: SLACK_SIGNING_SECRET is required when SLACK_BOT_TOKEN is set"
        if is_prod:
            errors.append(msg)
        else:
            warnings.append(msg)

    # PWA push: both VAPID keys required together
    if settings.vapid_private_key and not settings.vapid_public_key:
        msg = "  MISSING: VAPID_PUBLIC_KEY is required when VAPID_PRIVATE_KEY is set"
        if is_prod:
            errors.append(msg)
        else:
            warnings.append(msg)
    if settings.vapid_public_key and not settings.vapid_private_key:
        msg = "  MISSING: VAPID_PRIVATE_KEY is required when VAPID_PUBLIC_KEY is set"
        if is_prod:
            errors.append(msg)
        else:
            warnings.append(msg)

    if warnings:
        print("\n".join(["[Mimir] Config warnings:", *warnings]), file=sys.stderr)

    if errors:
        msg = "\n".join(
            ["[Mimir] Startup config validation FAILED:", *errors,
             "",
             "Fix the above issues or set MIMIR_ENV=development to skip prod checks."]
        )
        print(msg, file=sys.stderr)
        sys.exit(1)
