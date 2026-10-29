import httpx
import os
import pytest
from importlib import import_module
from scripts.shared.approvals import create_approval_request, request_research_approval
from scripts.shared.configuration import load_outputs, save_outputs
from scripts.shared.conversations import HostedClient
from typing import Any


pytestmark = [pytest.mark.hosted]


def load_hosted_targets(cloud: str) -> Any:
    from scripts.shared.invocations import read_metadata, require_deployment

    timestamp = load_required_timestamp("DEPLOYMENT_TIMESTAMP", cloud)
    metadata = read_metadata(require_deployment(cloud, timestamp))
    overrides = (
        {
            "DEPLOYMENT_NAME": metadata["deployment"],
            "AWS_ACCOUNT_ID": metadata["destination"],
            "AWS_REGION": metadata["region"],
        }
        if cloud == "aws"
        else {
            "DEPLOYMENT_NAME": metadata["deployment"],
            "GOOGLE_CLOUD_PROJECT": metadata["destination"],
            "GCP_REGION": metadata["region"],
        }
    )
    return import_module(f"scripts.{cloud}.configuration").load_targets(overrides)


def load_client_outputs(targets: Any, cloud: str) -> dict:
    timestamp = load_required_timestamp("DEPLOYMENT_TIMESTAMP", cloud)
    outputs = import_module(f"scripts.{cloud}.converse").load_client_outputs(timestamp)
    destination = targets.aws_account if cloud == "aws" else targets.gcp_project
    if (
        outputs.get("account" if cloud == "aws" else "project") != destination
        or outputs.get("deployment") != targets.deployment
    ):
        raise PermissionError("Hosted eval client outputs do not match selected target")
    return outputs


def load_required_timestamp(name: str, cloud: str) -> str:
    variable = f"{cloud.upper()}_{name}"
    timestamp = os.environ.get(variable)
    if not timestamp:
        raise ValueError(f"Hosted evals require {variable}")
    return timestamp


def login_demo_user(
    targets: Any, cloud: str, username: str, outputs: dict
) -> HostedClient:
    return import_module(f"scripts.{cloud}.converse").login_demo_user(
        targets, username, outputs
    )


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
def test_hosted_missing_credentials(cloud: str) -> None:
    targets = load_hosted_targets(cloud)
    client = login_demo_user(
        targets, cloud, "keyless", load_client_outputs(targets, cloud)
    )
    creation = client.invoke({"operation": "create_conversation"})
    assert creation["status"] == "idle", creation.get("error_code")
    response = client.invoke(
        {
            "operation": "send_message",
            "thread_id": creation["thread_id"],
            "message": "What is Brazil's FIFA code?",
        }
    )
    assert response["error_code"] == "credential_unavailable"
    history = client.invoke(
        {"operation": "read_conversation", "thread_id": creation["thread_id"]}
    )
    assert history["status"] == "idle" and history["messages"] == []
    save_outputs(
        f"{cloud}-verification",
        load_outputs(f"{cloud}-verification")
        | {
            "public_missing_model_secret": "credential_unavailable",
            "foreign_key_fallback": "denied",
            "missing_key_history": "empty",
        },
    )


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
def test_hosted_conversation_access(cloud: str) -> None:
    targets = load_hosted_targets(cloud)
    outputs = load_client_outputs(targets, cloud)
    alice = login_demo_user(targets, cloud, "alice", outputs)
    bob = login_demo_user(targets, cloud, "bob", outputs)
    creation = alice.invoke({"operation": "create_conversation"})
    assert creation["status"] == "idle", creation.get("error_code")
    thread_id = creation["thread_id"]
    reading = {"operation": "read_conversation", "thread_id": thread_id}
    assert alice.invoke(reading)["messages"] == []
    assert bob.invoke(reading)["error_code"] == "conversation_not_found"
    assert alice.invoke(reading | {"owner": "bob"})["error_code"] == "invalid_request"
    response = alice.invoke(
        {
            "operation": "send_message",
            "thread_id": thread_id,
            "message": "What is Brazil's FIFA code?",
        }
    )
    assert response["status"] == "idle", response.get("error_code")
    assert "BRA" in response["messages"][-1]["content"]
    history = alice.invoke(reading)
    # New public client/native runtime session inspects the same app-owned
    # thread, proving ownership does not derive from cached compute sessions.
    replacement = login_demo_user(targets, cloud, "alice", outputs)
    assert replacement.invoke(reading)["messages"] == history["messages"]

    proposal = request_research_approval(
        alice,
        thread_id,
        "Enrich the graph with the 2022 World Cup final score. "
        "Research authoritative sources and ask me for research approval first.",
    )
    persisted = replacement.invoke(reading)
    assert persisted["pending_approvals"] == proposal["pending_approvals"]
    actions = [
        action
        for approval in persisted["pending_approvals"]
        for action in approval["actions"]
    ]
    assert actions and all(action["name"] == "research_web" for action in actions)
    resume = create_approval_request(persisted, "reject")
    assert bob.invoke(resume)["error_code"] == "conversation_not_found"
    rejection = replacement.invoke(resume)
    assert rejection["status"] == "idle", rejection.get("error_code")
    assert alice.invoke(resume)["error_code"] == "invalid_approval"
    if cloud == "gcp":
        # Full-length UTF-8 messages fit the public and trusted-envelope budgets.
        assert (
            bob.invoke(
                {
                    "operation": "send_message",
                    "thread_id": thread_id,
                    "message": "🏆" * 16000,
                }
            )["error_code"]
            == "conversation_not_found"
        )
        with httpx.Client(trust_env=False, timeout=30) as client:
            response = client.post(outputs["gateway"] + "/invoke", json=reading)
        assert response.status_code == 401
    else:
        alice.token = "invalid-token"
        assert alice.invoke(reading)["error_code"] == "unauthenticated"
