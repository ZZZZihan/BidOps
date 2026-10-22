from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class JobsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BIDOPS_JOBS_", extra="ignore")
    enabled: bool = False
    max_active_jobs: int = Field(default=100, ge=1, le=100)
