from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScreeningSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BIDOPS_SCREENING_", extra="ignore")
    enabled: bool = False
    live_calls_enabled: bool = False
    # Deployment attestation after reconciling the CURRENT complete cumulative ledger.
    # This is deliberately false by default; an API payload cannot enable it.
    ledger_verified: bool = False
    ledger_restore_token: str | None = None
    api_key: SecretStr | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY", repr=False)
    model_name: Literal["deepseek-flash"] = "deepseek-flash"
    max_requests: int = Field(default=12, ge=1, le=30)
    max_seconds: float = Field(default=90, gt=0, le=300)
    max_segment_chars: int = Field(default=4000, ge=100, le=8000)
    max_request_bytes: int = Field(default=120000, ge=10000, le=200000)
    max_output_tokens: int = Field(default=6000, ge=100, le=8000)
    budget_cny: Decimal = Field(default=Decimal("10"), gt=0, le=10)
    input_cny_per_million: Decimal = Field(default=Decimal("2"), ge=2)
    output_cny_per_million: Decimal = Field(default=Decimal("8"), ge=8)
