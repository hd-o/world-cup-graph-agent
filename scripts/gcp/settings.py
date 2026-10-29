from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from scripts.shared.configuration import DEPLOY_ENV
from typing import Self


class GCPSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=DEPLOY_ENV,
        env_file_encoding="utf-8",
        extra="allow",
    )

    deployment_name: str = Field(
        default="worldcup-demo", pattern=r"^[a-z][a-z0-9-]{2,20}$"
    )
    google_cloud_project: str = Field(
        default="", pattern=r"^$|^[a-z][a-z0-9-]{4,28}[a-z0-9]$"
    )
    gcp_region: str = Field(default="", pattern=r"^$|^[a-z]+-[a-z]+[0-9]+$")

    demo_alice_openrouter_api_key: SecretStr = SecretStr("")
    demo_bob_openrouter_api_key: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_environment_file(self) -> Self:
        if DEPLOY_ENV.exists() and DEPLOY_ENV.stat().st_mode & 0o077:
            raise PermissionError("Protect deploy/.env with chmod 600 before use")
        return self

    def get_missing_routing_names(self) -> tuple[str, ...]:
        routing = {
            "google_cloud_project": self.google_cloud_project,
            "gcp_region": self.gcp_region,
        }
        return tuple(name for name, value in routing.items() if not value)

    def require_routing(self) -> Self:
        if self.get_missing_routing_names():
            raise ValueError(
                "Supply DEPLOYMENT_NAME, GOOGLE_CLOUD_PROJECT and GCP_REGION"
            )
        return self


def load_gcp_settings(overrides: dict[str, str] | None = None) -> GCPSettings:
    return GCPSettings(**(overrides or {}))


def get_gcp_inputs(settings: GCPSettings) -> dict[str, SecretStr]:
    routing_names = {"deployment_name", "google_cloud_project", "gcp_region"}
    return {
        name.upper(): value if isinstance(value, SecretStr) else SecretStr(str(value))
        for name, value in settings.model_dump().items()
        if name not in routing_names
    }
