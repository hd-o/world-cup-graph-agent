from pathlib import Path
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import ClassVar, Literal


type ProviderSort = Literal["latency", "throughput", "price"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: ClassVar[str] = "worldcup-agent"
    app_version: ClassVar[str] = "0.1.0"
    environment: str = Field(default="", min_length=1)
    runtime: Literal["local", "aws", "gcp"] = "local"
    deployment_name: str = Field(
        default="worldcup-demo", pattern=r"^[a-z][a-z0-9-]{2,20}$"
    )
    google_cloud_project: str = ""
    cognito_issuer: str = ""
    cognito_client_id: str = ""
    identity_tenant: str = ""

    # Hosted inference obtains its key from the verified user's managed secret.
    # Local credential resolution separately requires the fixture's global key.
    openrouter_api_key: SecretStr = SecretStr("")

    openrouter_supervisor_model_id: str = Field(default="", min_length=1)
    openrouter_supervisor_reasoning_effort: str = Field(default="", min_length=1)
    openrouter_supervisor_providers: list[str] = Field(default_factory=list)
    openrouter_supervisor_sort: ProviderSort = "throughput"

    openrouter_enricher_reasoning_effort: str = Field(default="", min_length=1)
    openrouter_enricher_model_id: str = Field(default="", min_length=1)
    openrouter_enricher_providers: list[str] = Field(default_factory=list)
    openrouter_enricher_sort: ProviderSort = "throughput"

    openrouter_research_model_id: str = Field(default="", min_length=1)
    openrouter_research_reasoning_effort: str = Field(default="", min_length=1)
    openrouter_research_providers: list[str] = Field(default_factory=list)
    openrouter_research_sort: ProviderSort = "throughput"

    neo4j_uri: str = Field(default="", pattern=r"^bolt://")
    neo4j_encrypted: bool = False
    neo4j_ca_certificate: Path | None = None
    neo4j_database: ClassVar[str] = "worldcup"
    neo4j_checkpoint_database: ClassVar[str] = "worldcup-checkpoints"

    neo4j_bootstrap_username: ClassVar[str] = "neo4j"
    neo4j_bootstrap_password: SecretStr = SecretStr("")

    neo4j_reader_username: ClassVar[str] = "worldcup_reader"
    neo4j_reader_password: SecretStr = SecretStr("")

    neo4j_writer_username: ClassVar[str] = "worldcup_writer"
    neo4j_writer_password: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_database_tls(self) -> Settings:
        if self.runtime != "local" and not self.neo4j_encrypted:
            raise ValueError("Hosted database connections require verified Bolt TLS")

        if self.neo4j_encrypted != (self.neo4j_ca_certificate is not None):
            raise ValueError("Bolt TLS requires encryption and a public CA certificate")

        return self
