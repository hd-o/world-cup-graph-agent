import json
from datetime import UTC, datetime
from scripts.aws.configuration import Targets
from scripts.aws.converse import login_demo_user
from scripts.aws.deployment import load_deployment
from scripts.shared.approvals import request_research_approval
from scripts.shared.configuration import save_outputs
from scripts.shared.interface import confirm_action, show_inventory
from scripts.shared.invocations import get_build_path


def stage_research_approval(
    targets: Targets, question: str, fact: str, execution: bool = False
) -> None:
    show_inventory(
        "AWS approval staging",
        {
            "deployment": targets.deployment,
            "question": question,
            "boundary": "Stage research approval only; no research/writes approved",
        },
    )
    confirm_action("Submit the approval-staging conversation?", execution=execution)
    client = login_demo_user(targets, "alice", load_deployment(targets))
    creation = client.invoke({"operation": "create_conversation"})
    if creation["status"] != "idle":
        raise RuntimeError("Conversation creation did not complete")
    response = request_research_approval(client, creation["thread_id"], question)
    manifest = json.loads(get_build_path("aws-manifest.json").read_text())
    save_outputs(
        "aws-approval-probe",
        {
            "deployment": targets.deployment,
            "destination": targets.aws_account,
            "thread_id": response["thread_id"],
            "pending_approvals": response["pending_approvals"],
            "source_sha256": manifest["sha256"],
            "question": question,
            "required_fact": fact,
            "staged_at": datetime.now(UTC).isoformat(),
        },
    )
    show_inventory(
        "AWS native approval staged",
        {"thread": response["thread_id"], "approved": False},
    )
