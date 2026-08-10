from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CONTENTFLOW_",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    app_name: str = "ContentFlow"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = (
        "postgresql+psycopg://contentflow:contentflow-local@localhost:5432/contentflow"
    )
    secret_key: str = "change-this-in-production"
    credential_encryption_key: str | None = None
    credential_encryption_previous_keys: list[str] = Field(default_factory=list)
    access_token_minutes: int = Field(default=15, ge=5, le=60)
    refresh_token_days: int = Field(default=14, ge=1, le=90)
    auth_token_issuer: str = "contentflow"
    auth_token_audience: str = "contentflow-api"
    access_cookie_name: str = "contentflow_access"
    refresh_cookie_name: str = "contentflow_refresh"
    auth_cookie_domain: str | None = None
    trusted_proxy_hops: int = Field(default=0, ge=0, le=10)
    auth_rate_limit_enabled: bool = True
    auth_rate_limit_window_seconds: int = Field(default=900, ge=60, le=86_400)
    auth_rate_limit_block_seconds: int = Field(default=900, ge=60, le=86_400)
    auth_login_account_attempts: int = Field(default=10, ge=1, le=10_000)
    auth_login_ip_attempts: int = Field(default=50, ge=1, le=100_000)
    auth_registration_ip_attempts: int = Field(default=20, ge=1, le=10_000)
    auth_refresh_session_attempts: int = Field(default=120, ge=1, le=100_000)
    auth_refresh_ip_attempts: int = Field(default=300, ge=1, le=100_000)
    allow_registration: bool = True
    allow_mock_providers: bool = False
    require_governed_prompts: bool = False
    metrics_enabled: bool = False
    metrics_bearer_token: str | None = Field(default=None, max_length=4096)
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://localhost:3300",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
            "http://127.0.0.1:3300",
            "http://127.0.0.1:5173",
        ]
    )

    storage_backend: str = "local"
    local_storage_dir: Path = Path(".contentflow/storage")
    max_upload_bytes: int = Field(default=100 * 1024 * 1024, gt=0, le=1024**3)
    public_base_url: str = "http://localhost:8000"
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "contentflow"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    text_provider: str = "mock"
    embedding_provider: str = "hash"
    image_provider: str = "mock"
    video_provider: str = "mock"
    model_api_base: str | None = None
    model_api_key: str | None = None
    text_model: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int = 1024

    media_api_base: str | None = None
    media_api_key: str | None = None
    media_download_allowed_hosts: list[str] = Field(default_factory=list)
    image_model: str | None = None
    video_model: str | None = None

    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_lease_seconds: int = Field(default=300, ge=3, le=86_400)
    worker_max_attempts: int = Field(default=4, ge=1, le=100)
    worker_heartbeat_seconds: int = Field(default=10, ge=1, le=300)
    worker_stale_seconds: int = Field(default=45, ge=3, le=1800)
    worker_queue_stall_seconds: int = Field(default=300, ge=10, le=86_400)
    publish_reconciliation_initial_delay_seconds: int = Field(default=15, ge=1, le=3600)
    publish_reconciliation_max_attempts: int = Field(default=20, ge=1, le=100)

    @field_validator("cors_origins", "media_download_allowed_hosts", mode="before")
    @classmethod
    def split_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_worker_monitoring_intervals(self):
        if self.worker_stale_seconds <= self.worker_heartbeat_seconds * 2:
            raise ValueError(
                "worker_stale_seconds must be greater than twice "
                "worker_heartbeat_seconds"
            )
        return self

    @field_validator(
        "storage_backend",
        "text_provider",
        "embedding_provider",
        "image_provider",
        "video_provider",
    )
    @classmethod
    def normalize_choice(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def auth_cookie_secure(self) -> bool:
        return self.production

    @property
    def resolved_auth_cookie_domain(self) -> str | None:
        return self.auth_cookie_domain or None

    @property
    def credential_encryption_primary_key(self) -> str:
        return self.credential_encryption_key or self.secret_key

    @property
    def credential_decryption_keys(self) -> tuple[str, ...]:
        candidates = [
            self.credential_encryption_primary_key,
            *self.credential_encryption_previous_keys,
            self.secret_key,
        ]
        return tuple(dict.fromkeys(key for key in candidates if key))

    def validate_runtime(self) -> None:
        if self.production and (
            self.secret_key == "change-this-in-production" or len(self.secret_key) < 32
        ):
            raise ValueError(
                "Production requires CONTENTFLOW_SECRET_KEY with at least 32 characters"
            )
        if self.production and not self.database_url.startswith("postgresql"):
            raise ValueError("Production requires a PostgreSQL database")
        if self.production and "*" in self.cors_origins:
            raise ValueError("Production does not allow a wildcard CORS origin")
        if self.production and not self.auth_rate_limit_enabled:
            raise ValueError("Production requires shared authentication rate limiting")
        if self.production and self.storage_backend != "s3":
            raise ValueError("Production requires S3-compatible object storage")
        if self.production and not self.require_governed_prompts:
            raise ValueError(
                "Production requires CONTENTFLOW_REQUIRE_GOVERNED_PROMPTS=true"
            )
        if self.production and not self.metrics_enabled:
            raise ValueError("Production requires CONTENTFLOW_METRICS_ENABLED=true")
        if self.metrics_enabled and (
            not self.metrics_bearer_token or len(self.metrics_bearer_token) < 32
        ):
            raise ValueError(
                "Metrics require CONTENTFLOW_METRICS_BEARER_TOKEN with at least "
                "32 characters"
            )
        if self.production and self.metrics_bearer_token in {
            self.secret_key,
            self.credential_encryption_key,
        }:
            raise ValueError(
                "Production metrics bearer token must be separate from application "
                "and credential encryption keys"
            )
        if self.production and (
            not self.credential_encryption_key
            or len(self.credential_encryption_key) < 32
            or self.credential_encryption_key == self.secret_key
        ):
            raise ValueError(
                "Production requires a separate "
                "CONTENTFLOW_CREDENTIAL_ENCRYPTION_KEY with at least 32 characters"
            )
        if any(len(key) < 32 for key in self.credential_encryption_previous_keys):
            raise ValueError(
                "Previous credential encryption keys must be at least 32 characters"
            )
        supported_providers = {
            "text": ({"mock", "openai-compatible"}, self.text_provider),
            "embedding": (
                {"hash", "openai-compatible"},
                self.embedding_provider,
            ),
            "image": ({"mock", "http"}, self.image_provider),
            "video": ({"mock", "http"}, self.video_provider),
        }
        invalid = [
            f"{kind}={provider}"
            for kind, (allowed, provider) in supported_providers.items()
            if provider not in allowed
        ]
        if invalid:
            raise ValueError(f"Unsupported providers: {', '.join(invalid)}")
        offline_providers = {
            "text": self.text_provider == "mock",
            "embedding": self.embedding_provider == "hash",
            "image": self.image_provider == "mock",
            "video": self.video_provider == "mock",
        }
        enabled_offline = [
            name for name, enabled in offline_providers.items() if enabled
        ]
        if self.production and enabled_offline and not self.allow_mock_providers:
            raise ValueError(
                "Production mock/hash providers require "
                "CONTENTFLOW_ALLOW_MOCK_PROVIDERS=true: " + ", ".join(enabled_offline)
            )
        uses_openai_compatible = (
            self.text_provider == "openai-compatible"
            or self.embedding_provider == "openai-compatible"
        )
        if uses_openai_compatible:
            required = {
                "CONTENTFLOW_MODEL_API_BASE": self.model_api_base,
                "CONTENTFLOW_MODEL_API_KEY": self.model_api_key,
            }
            if self.text_provider == "openai-compatible":
                required["CONTENTFLOW_TEXT_MODEL"] = self.text_model
            if self.embedding_provider == "openai-compatible":
                required["CONTENTFLOW_EMBEDDING_MODEL"] = self.embedding_model
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(
                    "OpenAI-compatible provider configuration missing: "
                    + ", ".join(missing)
                )
        uses_http_media = self.image_provider == "http" or self.video_provider == "http"
        if uses_http_media:
            required = {
                "CONTENTFLOW_MEDIA_API_BASE": self.media_api_base,
                "CONTENTFLOW_MEDIA_API_KEY": self.media_api_key,
                "CONTENTFLOW_MEDIA_DOWNLOAD_ALLOWED_HOSTS": (
                    self.media_download_allowed_hosts
                ),
            }
            if self.image_provider == "http":
                required["CONTENTFLOW_IMAGE_MODEL"] = self.image_model
            if self.video_provider == "http":
                required["CONTENTFLOW_VIDEO_MODEL"] = self.video_model
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(
                    "HTTP media provider configuration missing: " + ", ".join(missing)
                )
        if self.storage_backend == "s3":
            required = {
                "CONTENTFLOW_S3_ENDPOINT_URL": self.s3_endpoint_url,
                "CONTENTFLOW_S3_ACCESS_KEY": self.s3_access_key,
                "CONTENTFLOW_S3_SECRET_KEY": self.s3_secret_key,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"S3 配置不完整: {', '.join(missing)}")
        if (
            self.database_url.startswith("postgresql")
            and self.embedding_dimensions != 1024
        ):
            raise ValueError("当前 pgvector 迁移固定使用 1024 维向量")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    settings.local_storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
