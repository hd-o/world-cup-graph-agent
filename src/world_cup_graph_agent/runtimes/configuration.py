import os
from pathlib import Path
from pydantic import SecretStr
from typing import Literal
from world_cup_graph_agent.config.settings import Settings
from world_cup_graph_agent.infrastructure.secrets.managed import (
    read_database_credentials,
)


def load_hosted_settings(cloud: Literal["aws", "gcp"]) -> Settings:
    namespace = os.environ["DEPLOYMENT_NAME"]
    # Token issuers use the deployment's explicit project ID.
    project = os.getenv("DEPLOYMENT_GCP_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", ""))
    credentials = read_database_credentials(cloud, namespace, project)
    # Workers receive only the public CA; no database private material is written
    # to their filesystem. A fixed worker path is not a conversation identifier.
    certificate = Path("/tmp/worldcup-ca.pem")
    certificate.write_text(credentials["ca_certificate"])
    return Settings(
        _env_file=None,
        runtime=cloud,
        google_cloud_project=project,
        neo4j_encrypted=True,
        neo4j_ca_certificate=certificate,
        neo4j_reader_password=SecretStr(credentials["reader_password"]),
        neo4j_writer_password=SecretStr(credentials["writer_password"]),
        openrouter_api_key=SecretStr(""),
    )
