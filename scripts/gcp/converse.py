import httpx
import json
from scripts.gcp.configuration import Targets
from scripts.shared.conversations import HostedClient, converse


def load_client_outputs(
    timestamp: str,
) -> dict:
    # Public client configuration is a separate boundary: no operator target or
    # Terraform credentials are required for end-user conversation access.
    from scripts.shared.invocations import require_deployment

    folder = require_deployment("gcp", timestamp)
    outputs = json.loads((folder / "outputs/gcp.json").read_text())
    if any(not outputs.get(name) for name in ("gateway", "tenant", "login_key")):
        raise ValueError(
            "Supply gateway, tenant and login_key public connection settings"
        )
    return outputs


def converse_user(
    username: str, password: str, outputs: dict, thread_id: str | None = None
) -> None:
    client = login_user(username, password, outputs)
    converse(client, thread_id)


def login_user(username: str, password: str, outputs: dict) -> HostedClient:
    with httpx.Client(trust_env=False, timeout=30) as client:
        response = client.post(
            "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
            params={"key": outputs["login_key"]},
            json={
                "email": username,
                "password": password,
                "tenantId": outputs["tenant"],
                "returnSecureToken": True,
            },
        )
        if response.status_code != 200:
            raise PermissionError("Login failed")
        token = response.json()["idToken"]
    return HostedClient(outputs["gateway"] + "/invoke", token)


def login_demo_user(targets: Targets, username: str, outputs: dict) -> HostedClient:
    from scripts.gcp.enroll import read_demo_email
    from scripts.shared.enrollment import login_demo_user as login_enrolled_user

    validate_client_outputs(targets, outputs)
    return login_enrolled_user(
        targets, username, outputs, login_user, read_demo_email(targets, username)
    )


def validate_client_outputs(targets: Targets, outputs: dict) -> None:
    if (outputs.get("project"), outputs.get("region"), outputs.get("deployment")) != (
        targets.gcp_project,
        targets.gcp_region,
        targets.deployment,
    ):
        raise PermissionError(
            "Client configuration does not match the selected GCP target"
        )
