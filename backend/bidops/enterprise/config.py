from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnterpriseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BIDOPS_ENTERPRISE_", extra="ignore", hide_input_in_errors=True
    )
    enabled: bool = False
    public_url: str = "https://bidops.example.invalid"
    oidc_issuer: str = ""
    oidc_client_id: str = "bidops"
    oidc_client_secret: SecretStr | None = Field(default=None, repr=False)
    cookie_secret: SecretStr | None = Field(default=None, repr=False)
    allow_insecure_loopback: bool = False
    session_seconds: int = Field(default=3600, ge=60, le=28800)
    identity_snapshot_seconds: int = Field(default=240, ge=30, le=240)
    identity_provider: Literal["keycloak"] = "keycloak"
    history_days: int = Field(default=30, ge=1, le=365)
    requests_per_minute: int = Field(default=20, ge=1, le=120)
    organization: str = Field(default="BidOps", min_length=1, max_length=80)
    deployment: Literal["single_organization_single_worker"] = "single_organization_single_worker"

    @model_validator(mode="after")
    def validate_enterprise(self):
        if not self.enabled:
            return self
        if not self.cookie_secret or len(self.cookie_secret.get_secret_value()) < 32:
            raise ValueError("Enterprise mode requires a random cookie secret of at least 32 chars")
        if not self.oidc_client_id or not self.oidc_client_secret:
            raise ValueError("Enterprise mode requires an OIDC confidential client")
        for value in (self.public_url, self.oidc_issuer):
            u = urlsplit(value)
            loopback = self.allow_insecure_loopback and u.hostname in {"localhost", "127.0.0.1"}
            if (
                not u.hostname
                or u.username
                or u.password
                or u.query
                or u.fragment
                or (u.scheme != "https" and not (u.scheme == "http" and loopback))
            ):
                raise ValueError("Enterprise URLs require HTTPS (HTTP only for explicit loopback)")
        if urlsplit(self.public_url).path not in {"", "/"}:
            raise ValueError("public_url must be an origin, without a path")
        self.public_url = self.public_url.rstrip("/")
        self.oidc_issuer = self.oidc_issuer.rstrip("/")
        return self

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")
