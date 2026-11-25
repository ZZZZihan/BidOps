from pydantic_settings import BaseSettings, SettingsConfigDict


class ReviewSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BIDOPS_REVIEW_", extra="ignore")
    enabled: bool = False
