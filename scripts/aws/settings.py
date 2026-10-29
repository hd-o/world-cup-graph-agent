from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from scripts.shared.configuration import DEPLOY_ENV
from typing import Self


class AWSSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=DEPLOY_ENV,
        env_file_encoding="utf-8",
        extra="allow",
    )

    deployment_name: str = Field(
        default="worldcup-demo", pattern=r"^[a-z][a-z0-9-]{2,20}$"
    )
    aws_account_id: str = Field(default="", pattern=r"^$|^\d{12}$")
    aws_profile: str = ""
    aws_region: str = Field(default="", pattern=r"^$|^[a-z]{2}(?:-[a-z]+)+-\d$")

    demo_alice_openrouter_api_key: SecretStr = SecretStr("")
    demo_bob_openrouter_api_key: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_environment_file(self) -> Self:
        if DEPLOY_ENV.exists() and DEPLOY_ENV.stat().st_mode & 0o077:
            raise PermissionError("Protect deploy/.env with chmod 600 before use")
        return self

    def get_missing_routing_names(self) -> tuple[str, ...]:
        routing = {
            "aws_profile": self.aws_profile,
            "aws_account_id": self.aws_account_id,
            "aws_region": self.aws_region,
        }
        return tuple(name for name, value in routing.items() if not value)

    def require_routing(self) -> Self:
        if self.get_missing_routing_names():
            raise ValueError(
                "AWS requires deployment, profile, account and region inputs"
            )
        return self


def load_aws_settings(overrides: dict[str, str] | None = None) -> AWSSettings:
    return AWSSettings(**(overrides or {}))


def get_aws_inputs(settings: AWSSettings) -> dict[str, SecretStr]:
    routing_names = {
        "deployment_name",
        "aws_account_id",
        "aws_profile",
        "aws_region",
    }
    return {
        name.upper(): value if isinstance(value, SecretStr) else SecretStr(str(value))
        for name, value in settings.model_dump().items()
        if name not in routing_names
    }
