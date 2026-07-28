from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
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
    database_url: str = "sqlite:///./.contentflow/contentflow-v2.db"
    secret_key: str = "change-this-in-production"
    access_token_minutes: int = 480
    allow_registration: bool = True
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
    text_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024

    dashscope_workspace_id: str | None = None
    dashscope_api_key: str | None = None
    dashscope_region: str = "beijing"
    dashscope_image_model: str = "wan2.6-t2i"
    dashscope_video_model: str = "wan2.7-t2v-2026-06-12"

    worker_poll_seconds: float = 1.0
    worker_lease_seconds: int = 300
    worker_max_attempts: int = 4

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

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

    def validate_runtime(self) -> None:
        if self.production and self.secret_key == "change-this-in-production":
            raise ValueError("生产环境必须设置 CONTENTFLOW_SECRET_KEY")
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
