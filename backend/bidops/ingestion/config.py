from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BIDOPS_INGESTION_", extra="ignore")
    enabled: bool = False
    max_active_runs: int = Field(default=10, ge=1, le=100)
    max_schedules: int = Field(default=20, ge=1, le=100)
    freshness_seconds: int = Field(default=86400, ge=60, le=604800)
