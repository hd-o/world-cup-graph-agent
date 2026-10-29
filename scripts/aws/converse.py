import httpx
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from pydantic import SecretStr
from scripts.aws.configuration import Targets
from scripts.shared.conversations import (
    HostedClient,
    converse,
    validate_approval_decisions,
)
from scripts.shared.enrollment import read_user_input
from scripts.shared.interface import (
    ask_password,
    ask_text,
    confirm_action,
    require_terminal,
    show_inventory,
)
from urllib.parse import quote


@dataclass(frozen=True)
class ClientInputs:
    deployment: str
    inputs: dict[str, SecretStr] = field(repr=False)


def login_user(username: str, password: str, outputs: dict) -> HostedClient:
    region = outputs["region"]
    with httpx.Client(trust_env=False, timeout=30) as client:
        response = client.post(
            f"https://cognito-idp.{region}.amazonaws.com/",
            headers={
                "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
                "Content-Type": "application/x-amz-json-1.1",
            },
            json={
                "AuthFlow": "USER_PASSWORD_AUTH",
                "ClientId": outputs["ClientId"],
                "AuthParameters": {"USERNAME": username, "PASSWORD": password},
            },
        )
    if response.status_code != 200 or "AuthenticationResult" not in response.json():
        raise PermissionError("Login failed")
    token = response.json()["AuthenticationResult"]["AccessToken"]
    resource = quote(outputs["RuntimeArn"], safe="")
    return HostedClient(
        endpoint=f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{resource}/invocations",
        token=token,
        parameters={"qualifier": "DEFAULT"},
        session_header="X-Amzn-Bedrock-AgentCore-Runtime-Session-Id",
    )


def load_client_outputs(
    timestamp: str,
) -> dict:
    from scripts.shared.invocations import require_deployment

    folder = require_deployment("aws", timestamp)
    outputs = json.loads((folder / "outputs/aws.json").read_text())
    if any(not outputs.get(name) for name in ("region", "RuntimeArn", "ClientId")):
        raise ValueError(
            "Client access requires public region, runtime ARN and Cognito client ID"
        )
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", outputs["region"]):
        raise ValueError("Public AWS client region is invalid")
    if not re.fullmatch(
        rf"arn:aws:bedrock-agentcore:{re.escape(outputs['region'])}:\d{{12}}:runtime/[A-Za-z0-9_-]+",
        outputs["RuntimeArn"],
    ):
        raise ValueError("Public runtime ARN must match the AWS client region")
    return outputs


def login_demo_user(targets: Targets, username: str, outputs: dict) -> HostedClient:
    from scripts.shared.enrollment import load_login_password

    if (
        outputs["region"] != targets.aws_region
        or outputs["RuntimeArn"].split(":")[4] != targets.aws_account
        or outputs.get("deployment", targets.deployment) != targets.deployment
    ):
        raise PermissionError(
            "Demo client connection does not match the selected AWS target"
        )
    return login_user(username, load_login_password(targets, username), outputs)


def converse_user(
    timestamp: str,
    username: str | None,
    thread: str | None,
    request: Path | None = None,
    execution: bool = False,
) -> None:
    from scripts.aws.settings import get_aws_inputs, load_aws_settings

    if request is None:
        require_terminal()
    operation = json.loads(request.read_text()) if request is not None else None
    if operation is not None and operation.get("operation") not in (
        "read_conversation",
        "list_conversations",
    ):
        confirm_action("Submit the explicit conversation request?", execution=execution)
    outputs = load_client_outputs(timestamp)
    username = username or ask_text("AWS username")
    # Client login reads protected input but never authenticates an AWS operator.
    inputs = ClientInputs("", get_aws_inputs(load_aws_settings()))
    password = read_user_input(inputs, username, "password") or ask_password(
        "AWS password"
    )
    client = login_user(username, password, outputs)
    if operation is not None:
        if operation.get("operation") == "resume_conversation":
            response = client.invoke(
                {
                    "operation": "read_conversation",
                    "thread_id": operation["thread_id"],
                }
            )
            show_inventory("Exact pending native approval cards", response)
            if response["status"] != "approval_required":
                return
            validate_approval_decisions(
                response["pending_approvals"], operation.get("approvals", [])
            )
        response = client.invoke(operation)
        show_inventory("AWS conversation response", response)
    else:
        converse(client, thread)
