"""Application settings — dibaca dari env / .env (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "corebank-api"
    app_env: str = "dev"  # dev | test | prod
    log_level: str = "INFO"

    # URL gaya SQLAlchemy; psycopg butuh pola murni → pakai properti psycopg_dsn
    database_url: str = "postgresql+psycopg://corebank:corebank@localhost:5432/corebank"
    redis_url: str = "redis://localhost:6379/0"
    amqp_url: str = "amqp://guest:guest@localhost:5672/"

    @property
    def psycopg_dsn(self) -> str:
        return self.database_url.replace("postgresql+psycopg://", "postgresql://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
