import structlog
from aiomisc import timeout
from enum import StrEnum
from evals.runtime import ConversationRunner, ConversationState
from langchain.agents.middleware.human_in_the_loop import ActionRequest
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command


logger = structlog.get_logger(__name__)


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@timeout(300)
async def send_message(
    conversation_runner: ConversationRunner, config: RunnableConfig, content: str
) -> ConversationState:
    return await conversation_runner(
        {"messages": [{"role": "user", "content": content}]}, config
    )


async def request_research_approval(
    conversation_runner: ConversationRunner,
    config: RunnableConfig,
    conversation_state: ConversationState,
) -> ConversationState:
    """Answer conversational clarification without replacing a pending card."""
    for reply in (
        "Yes, please research it",
        "Yes, I agree with this plan, please go ahead",
    ):
        if conversation_state.get("__interrupt__"):
            break

        conversation_state = await send_message(conversation_runner, config, reply)

    actions = _read_pending_actions(conversation_state)
    assert {action["name"] for action in actions} == {"research_web"}, (
        "Expected research approval before any mutation approval"
    )

    return conversation_state


@timeout(300)
async def submit_approval_decision(
    conversation_runner: ConversationRunner,
    config: RunnableConfig,
    conversation_state: ConversationState,
    decision: ApprovalDecision,
) -> ConversationState:
    actions = _read_pending_actions(conversation_state)
    action_names = {action["name"] for action in actions}

    # Research findings must exist before mutation is proposed. Approving a
    # mixed batch would conceal a violation of that sequence.
    assert action_names in ({"research_web"}, {"mutate_graph"}), (
        f"Unexpected approval actions: {sorted(action_names)}"
    )

    logger.info(
        "eval_approval_decision",
        scenario=config["metadata"]["eval_scenario"],
        actions=sorted(action_names),
        decision=decision.value,
    )

    return await conversation_runner(
        Command(resume={"decisions": [{"type": decision.value} for _ in actions]}),
        config,
    )


async def complete_approved_enrichment(
    conversation_runner: ConversationRunner,
    config: RunnableConfig,
    conversation_state: ConversationState,
) -> ConversationState:
    """Approve bounded research and mutation rounds, then require completion."""
    # Empty live findings can need further research under a fresh approval.
    # The cap prevents a looping agent from consuming unbounded paid calls.
    for _ in range(4):
        if not conversation_state.get("__interrupt__"):
            return conversation_state

        conversation_state = await submit_approval_decision(
            conversation_runner, config, conversation_state, ApprovalDecision.APPROVE
        )

    assert not conversation_state.get("__interrupt__"), (
        "Enrichment exceeded four approval rounds"
    )
    return conversation_state


def _read_pending_actions(conversation_state: ConversationState) -> list[ActionRequest]:
    interrupts = conversation_state.get("__interrupt__", ())
    assert len(interrupts) == 1, (
        f"Expected one enrichment approval interrupt, received {len(interrupts)}"
    )

    actions = interrupts[0].value["action_requests"]
    assert actions, "Approval interrupt contained no action requests"

    return actions
