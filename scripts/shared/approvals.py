import json
from datetime import UTC, datetime
from scripts.shared.configuration import save_outputs
from scripts.shared.conversations import HostedClient


def request_research_approval(
    client: HostedClient, thread_id: str, question: str
) -> dict:
    response = client.invoke(
        {"operation": "send_message", "thread_id": thread_id, "message": question}
    )
    # Plan confirmation never submits a native tool decision. Uncertain/error
    # responses stop immediately rather than granting execution replay.
    for confirmation in (
        "Yes, please research it. Stop at the research approval gate.",
        "I agree with the research plan. Request research_web approval now; "
        "do not perform research or graph writes without the native approval.",
    ):
        if response["status"] != "idle":
            break
        response = client.invoke(
            {
                "operation": "send_message",
                "thread_id": thread_id,
                "message": confirmation,
            }
        )
    if response["status"] != "approval_required":
        raise RuntimeError(
            response.get("error_code")
            or "Research approval not reached; choose a genuine graph gap"
        )
    actions = [
        action
        for approval in response["pending_approvals"]
        for action in approval["actions"]
    ]
    if not actions or any(action["name"] != "research_web" for action in actions):
        raise RuntimeError(
            "Expected only native research approval; no decision submitted"
        )
    return response


def create_approval_request(response: dict, decision: str) -> dict:
    if decision not in ("approve", "reject"):
        raise ValueError("Choose approve or reject; no operation submitted")
    return {
        "operation": "resume_conversation",
        "thread_id": response["thread_id"],
        "approvals": [
            {
                "approval_id": approval["approval_id"],
                "decisions": [{"type": decision} for _ in approval["actions"]],
            }
            for approval in response["pending_approvals"]
        ],
    }


def stage_research_approval(
    client: HostedClient,
    question: str,
    fact: str,
    name: str,
    deployment: str,
    destination: str,
) -> dict:
    creation = client.invoke({"operation": "create_conversation"})
    if creation["status"] != "idle":
        raise RuntimeError(creation.get("error_code"))
    response = request_research_approval(client, creation["thread_id"], question)
    from scripts.shared.invocations import get_build_path

    manifest = json.loads(get_build_path(f"{name}-manifest.json").read_text())
    probe = {
        "deployment": deployment,
        "destination": destination,
        "thread_id": response["thread_id"],
        "pending_approvals": response["pending_approvals"],
        "source_sha256": manifest["sha256"],
        "question": question,
        "required_fact": fact,
        "staged_at": datetime.now(UTC).isoformat(),
    }
    save_outputs(f"{name}-approval-probe", probe)
    return probe
