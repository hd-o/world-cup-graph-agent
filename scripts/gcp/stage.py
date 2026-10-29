import json
from datetime import UTC, datetime
from scripts.gcp.configuration import Targets
from scripts.gcp.converse import (
    login_demo_user,
    validate_client_outputs,
)
from scripts.shared.approvals import request_research_approval
from scripts.shared.configuration import save_outputs
from scripts.shared.invocations import get_build_path


def stage_research_approval(targets: Targets, question: str, fact: str) -> None:
    from scripts.gcp.deployment import load_deployment_outputs

    outputs = load_deployment_outputs(targets)
    validate_client_outputs(targets, outputs)
    client = login_demo_user(targets, "alice", outputs)
    creation = client.invoke({"operation": "create_conversation"})
    if creation["status"] != "idle":
        raise RuntimeError(creation.get("error_code", "Conversation creation failed"))
    response = request_research_approval(client, creation["thread_id"], question)
    manifest = json.loads(get_build_path("gcp-manifest.json").read_text())
    save_outputs(
        "gcp-approval-probe",
        {
            "deployment": targets.deployment,
            "destination": targets.gcp_project,
            "thread_id": response["thread_id"],
            "pending_approvals": response["pending_approvals"],
            "source_sha256": manifest["sha256"],
            "question": question,
            "required_fact": fact,
            "staged_at": datetime.now(UTC).isoformat(),
        },
    )
