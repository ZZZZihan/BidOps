from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BIDOPS_", extra="ignore")
    data_dir: Path = Path("data")
    database_url: str = "sqlite:///./data/processed/bidops.sqlite3"
    source_registry: Path = Path("config/sources.json")
    network_enabled: bool = False
    http_timeout_seconds: float = Field(default=15, gt=0, le=60)
    http_rate_limit_seconds: float = Field(default=5, ge=5)
    http_attempts: int = Field(default=3, ge=1, le=3)
    max_bytes: int = Field(default=2_000_000, gt=0, le=10_000_000)
    user_agent: str = "BidOps-P0/0.1 (+https://github.com/ZZZZihan/BidOps)"
