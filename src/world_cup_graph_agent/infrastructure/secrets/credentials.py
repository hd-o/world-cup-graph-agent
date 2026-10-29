from dataclasses import dataclass
from pydantic import SecretStr
from world_cup_graph_agent.config.settings import Settings


@dataclass(frozen=True)
class Principal:
    identity: str


class CredentialError(RuntimeError):
    pass


async def get_openrouter_api_key(principal: Principal, settings: Settings) -> SecretStr:
    # Fixtures: both identities share the operator's key.
    # The principal comes from authentication, never request payload/config.
    # Keep the async boundary for a credential store/KMS integration, where
    # lookup and decryption must bind the secret to this trusted principal.
    if (
        principal.identity not in ("alice", "bob")
        or settings.environment != "local"
        or settings.runtime != "local"
        or not settings.openrouter_api_key.get_secret_value()
    ):
        raise CredentialError("Credential unavailable")

    return settings.openrouter_api_key
