import json
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from pydantic import ValidationError
from world_cup_graph_agent.tools.mutate_graph.models import MutateGraphSuccess


# Refused replies open with this marker so past refusals stay in history.
_REFUSAL_PREFIX = "research_web refused:"

# Matches the installed HumanInTheLoopMiddleware rejection wording,
# the only artifact a rejected call leaves in history.
_REJECTED_PREFIX = "User rejected the tool call for `"

_REJECTED_RESEARCH_REFUSAL = (
    f"{_REFUSAL_PREFIX} the research approval for this enrichment run was "
    "rejected. Return the GraphEnrichmentReport with status "
    "research_rejected immediately and call no protected tool again in this "
    "run."
)

_REUSABLE_RESEARCH_REFUSAL = (
    f"{_REFUSAL_PREFIX} an approved research result for this gap already "
    "exists in this conversation. Do not call research_web again. Use those "
    "findings as the mutation evidence and propose one mutate_graph call "
    "carrying the complete ordered operations."
)


def should_request_research_approval(request: ToolCallRequest) -> bool:
    """Judge whether a research_web call still needs its approval card."""
    return _find_research_refusal(_read_conversation(request.state)) is None


class EnricherResearchGuard(AgentMiddleware):
    """Refuse a blocked enricher research_web call before the tool executes."""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ):
        refusal = _build_refusal_tool_message(request)
        if refusal is not None:
            return refusal

        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ):
        refusal = _build_refusal_tool_message(request)
        if refusal is not None:
            return refusal

        return await handler(request)


def _read_conversation(state: object) -> list[BaseMessage]:
    """Read the enricher message history that the guard hooks observe."""
    if isinstance(state, dict):
        messages = state.get("messages", [])
        return messages if isinstance(messages, list) else []

    # Tool runs without graph state carry no history; the guard then keeps
    # the normal approval card instead of refusing.
    return []


def _find_research_refusal(conversation: list[BaseMessage]) -> str | None:
    """Return the refusal text for a further enricher research_web call.

    The refusal covers the phase since the last applied mutate_graph
    result; earlier phases are outside this run because each delegation
    starts a fresh history.
    """
    if any(_is_rejected_call(message) for message in conversation):
        return _REJECTED_RESEARCH_REFUSAL

    scope = conversation[_find_applied_mutation_position(conversation) + 1 :]

    if any(
        isinstance(message, ToolMessage)
        and message.name == "research_web"
        and _has_sourced_findings(message)
        for message in scope
    ):
        return _REUSABLE_RESEARCH_REFUSAL

    return None


def _build_refusal_tool_message(request: ToolCallRequest) -> ToolMessage | None:
    """Build the short refusal reply for a research_web call the guard blocks."""
    if request.tool_call["name"] != "research_web":
        return None

    refusal = _find_research_refusal(_read_conversation(request.state))
    if refusal is None:
        return None

    # status="error" marks the call as not executed; the reply text never
    # replays the stored findings or their evidence.
    return ToolMessage(
        content=refusal,
        name="research_web",
        tool_call_id=request.tool_call["id"],
        status="error",
    )


def _is_rejected_call(message: BaseMessage) -> bool:
    """Recognize the HITL wording that replaced an unapproved research call."""
    prefix = f"{_REJECTED_PREFIX}{'research_web'}`"

    return (
        isinstance(message, ToolMessage)
        and message.name == "research_web"
        and isinstance(message.content, str)
        and message.content.startswith(prefix)
    )


def _find_applied_mutation_position(conversation: list[BaseMessage]) -> int:
    """Return the position of the last applied mutate_graph result."""
    position = -1

    for index, message in enumerate(conversation):
        if not _is_applied_mutation_result(message):
            continue

        position = index

    return position


def _is_applied_mutation_result(message: BaseMessage) -> bool:
    """Judge whether a mutate_graph reply parsed as an applied success.

    Only `MutateGraphSuccess` ends a guarded phase; every other reply
    keeps the refusal block in place.
    """
    if not isinstance(message, ToolMessage) or message.name != "mutate_graph":
        return False

    if not isinstance(message.content, str):
        return False

    try:
        MutateGraphSuccess.model_validate_json(message.content)
    except ValidationError:
        return False

    return True


def _has_sourced_findings(reply: ToolMessage) -> bool:
    """Judge whether one research reply carries a nonempty findings list."""
    if not isinstance(reply.content, str):
        return False

    try:
        payload = json.loads(reply.content)
    except json.JSONDecodeError:
        return False

    # Only a nonempty list is checked; each finding's sources stay
    # unvalidated.
    findings = payload.get("findings") if isinstance(payload, dict) else None

    return isinstance(findings, list) and bool(findings)
