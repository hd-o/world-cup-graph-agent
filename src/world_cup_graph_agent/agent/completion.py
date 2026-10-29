from collections.abc import Sequence
from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import BaseMessage, ToolMessage
from langgraph.runtime import Runtime
from pydantic import ValidationError
from typing import Any
from world_cup_graph_agent.enrichment.models import GraphEnrichmentReport
from world_cup_graph_agent.tools.mutate_graph.models import (
    MutateGraphSuccess,
    MutationOutcomeState,
)


class EnrichmentCompletion(AgentMiddleware):
    @hook_config(can_jump_to=["end"])
    def before_model(
        self, state: AgentState, runtime: Runtime
    ) -> dict[str, Any] | None:
        return build_completion_update(state["messages"])

    @hook_config(can_jump_to=["end"])
    async def abefore_model(
        self, state: AgentState, runtime: Runtime
    ) -> dict[str, Any] | None:
        return build_completion_update(state["messages"])


def build_completion_update(messages: Sequence[BaseMessage]) -> dict[str, Any] | None:
    # Each delegation has its own history. Only actual native rejection or a
    # typed applied mutation establishes completion; model prose cannot do so.
    # End via the public middleware hook before further inference can loop or
    # turn research details into the supervisor's orchestration report.
    for message in reversed(messages):
        if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
            continue
        if message.name not in ("research_web", "mutate_graph"):
            continue
        if message.status == "error" and message.content.startswith(
            f"User rejected the tool call for `{message.name}`"
        ):
            report = GraphEnrichmentReport(
                status="research_rejected"
                if message.name == "research_web"
                else "mutation_rejected",
                summary=f"User rejected {message.name}; action not executed.",
                changed_ids=[],
            )
            return {"structured_response": report, "jump_to": "end"}
        if message.name != "mutate_graph" or message.status != "success":
            continue
        try:
            mutation = MutateGraphSuccess.model_validate_json(message.content)
        except ValidationError:
            # Validation/transaction failures leave the agent's repair and
            # separate approval workflow intact; they never imply success.
            continue
        outcomes = [
            outcome
            for outcome in mutation.outcomes
            if outcome.result == MutationOutcomeState.CREATED
        ]
        report = GraphEnrichmentReport(
            status="enriched" if outcomes else "no_change",
            summary="Approved mutation applied."
            if outcomes
            else "Requested mutation is already present.",
            changed_ids=[
                outcome.target_id
                for outcome in outcomes
                if outcome.target_id is not None
            ],
        )
        return {"structured_response": report, "jump_to": "end"}
    return None
