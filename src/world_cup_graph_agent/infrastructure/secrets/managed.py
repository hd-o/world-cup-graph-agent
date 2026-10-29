import asyncio
import json
from pydantic import SecretStr
from typing import Any
from world_cup_graph_agent.infrastructure.secrets.credentials import CredentialError
from world_cup_graph_agent.runtimes.contracts import HostedPrincipal


class UserSecrets:
    def __init__(self, cloud: str, namespace: str, project: str = "") -> None:
        self.cloud = cloud
        self.namespace = namespace
        self.project = project
        self.aws: Any = None
        self.google: Any = None
        if cloud == "aws":
            import boto3
            from botocore.config import Config

            self.aws = boto3.client(
                "secretsmanager", config=Config(retries={"max_attempts": 0})
            )
        else:
            from google.cloud import secretmanager

            self.google = secretmanager.SecretManagerServiceClient()

    async def get_openrouter_api_key(self, principal: HostedPrincipal) -> SecretStr:
        return await asyncio.to_thread(self.read_openrouter_api_key, principal)

    def read_openrouter_api_key(self, principal: HostedPrincipal) -> SecretStr:
        identity = principal.get_identity()
        try:
            if self.aws is not None:
                response = self.aws.get_secret_value(
                    SecretId=f"{self.namespace}/users/{identity}/openrouter"
                )
                value = response["SecretString"]
            elif self.google is not None:
                response = self.google.access_secret_version(
                    request={
                        "name": f"projects/{self.project}/secrets/"
                        f"{self.namespace}-users-{identity}/versions/latest"
                    }
                )
                value = response.payload.data.decode()
            else:
                raise CredentialError("credential_unavailable")
        except Exception as error:
            raise CredentialError("credential_unavailable") from error

        if not value.strip():
            raise CredentialError("credential_unavailable")

        return SecretStr(value)


def read_database_credentials(cloud: str, namespace: str, project: str) -> dict:
    # Runtime permissions cover only reader/writer material. The admin secret
    # and database TLS key are separate resources unavailable to agent workers.
    if cloud == "aws":
        import boto3

        response = boto3.client("secretsmanager").get_secret_value(
            SecretId=f"{namespace}/database-runtime"
        )
        return json.loads(response["SecretString"])

    from google.cloud import secretmanager

    response = secretmanager.SecretManagerServiceClient().access_secret_version(
        request={
            "name": f"projects/{project}/secrets/"
            f"{namespace}-database-runtime/versions/latest"
        }
    )
    return json.loads(response.payload.data.decode())
