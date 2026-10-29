import httpx
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4
from world_cup_graph_agent.runtimes.contracts import ResponseEnvelope


@dataclass
class HostedClient:
    endpoint: str
    token: str = field(repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    parameters: dict[str, str] = field(default_factory=dict, repr=False)
    session_header: str | None = None
    session_id: str = field(default_factory=lambda: str(uuid4()))

    def invoke(self, request: dict) -> dict:
        headers = self.headers | {"Authorization": f"Bearer {self.token}"}
        if self.session_header:
            headers[self.session_header] = self.session_id
        try:
            with httpx.Client(trust_env=False, timeout=560) as client:
                response = client.post(
                    self.endpoint,
                    json=request,
                    params=self.parameters,
                    headers=headers,
                )
        except httpx.TransportError:
            # A lost response never grants replay permission. Inspect the durable
            # thread using a fresh authenticated client instead of resubmitting.
            return {"status": "uncertain", "error_code": "execution_uncertain"}
        try:
            envelope = ResponseEnvelope.model_validate(response.json())
        except ValueError:
            # Preserve contract envelopes, but never display arbitrary platform
            # bodies that can contain credentials or untrusted exception text.
            return {
                "status": "error",
                "error_code": "unauthenticated"
                if response.status_code in (401, 403)
                else "runtime_unavailable",
            }
        return envelope.model_dump(mode="json")


def read_conversation_message() -> str:
    from scripts.shared.interface import ask_text

    return ask_text("You (/read, /list, /quit)")


def decide_conversation_approvals(approvals: list[dict]) -> list[dict]:
    from scripts.shared.interface import choose_action, show_inventory

    decisions = []
    for approval in approvals:
        actions = []
        for action in approval["actions"]:
            show_inventory(f"Approval {approval['approval_id']}", action)
            actions.append({"type": choose_action(["approve", "reject"])})
        decisions.append({"approval_id": approval["approval_id"], "decisions": actions})
    return decisions


def display_conversation_response(response: dict) -> None:
    from scripts.shared.interface import show_inventory

    show_inventory("Conversation", response)


def converse(
    client: HostedClient,
    thread_id: str | None,
    message_reader: Callable[[], str] = read_conversation_message,
    approval_decision: Callable[[list[dict]], list[dict]] = (
        decide_conversation_approvals
    ),
    response_display: Callable[[dict], None] = display_conversation_response,
) -> None:
    if thread_id is None:
        creation = client.invoke({"operation": "create_conversation"})
        response_display(creation)
        thread_id = creation.get("thread_id")
        if creation["status"] != "idle" or thread_id is None:
            return
    while True:
        response = client.invoke(
            {"operation": "read_conversation", "thread_id": thread_id}
        )
        response_display(response)
        if response["status"] in ("active", "uncertain", "error"):
            return
        approvals = response.get("pending_approvals", [])
        if approvals:
            decisions = approval_decision(approvals)
            validate_approval_decisions(approvals, decisions)
            response = client.invoke(
                {
                    "operation": "resume_conversation",
                    "thread_id": thread_id,
                    "approvals": decisions,
                }
            )
        else:
            message = message_reader().strip()
            if message == "/quit":
                return
            if message in ("/read", "/list"):
                operation = (
                    {"operation": "list_conversations"}
                    if message == "/list"
                    else {"operation": "read_conversation", "thread_id": thread_id}
                )
                inspection = client.invoke(operation)
                response_display(inspection)
                if inspection["status"] in ("error", "uncertain", "active"):
                    return
                continue
            if not message:
                continue
            response = client.invoke(
                {
                    "operation": "send_message",
                    "thread_id": thread_id,
                    "message": message,
                }
            )
        response_display(response)
        if response["status"] in ("error", "uncertain", "active"):
            return


def validate_approval_decisions(approvals: list[dict], decisions: list[dict]) -> None:
    if len(approvals) != len(decisions):
        raise ValueError("Choose a decision for every approval; no operation submitted")
    for approval, decision in zip(approvals, decisions, strict=True):
        actions = decision.get("decisions", [])
        if (
            set(decision) != {"approval_id", "decisions"}
            or decision["approval_id"] != approval["approval_id"]
            or len(actions) != len(approval["actions"])
            or any(
                action not in ({"type": "approve"}, {"type": "reject"})
                for action in actions
            )
        ):
            raise ValueError(
                "Choose ordered approve/reject decisions; no operation submitted"
            )
