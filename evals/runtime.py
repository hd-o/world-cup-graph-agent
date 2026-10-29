import structlog
from collections.abc import Awaitable, Callable
from evals.reporting.metadata import build_eval_metadata
from evals.settings import EvalSettings
from langchain_core.runnables import RunnableConfig
from langchain_neo4j import AsyncNeo4jSaver
from langgraph.types import Command
from typing import Any
from world_cup_graph_agent.agent.factory import create_app_agent
from world_cup_graph_agent.infrastructure.neo4j.driver import GraphDrivers
from world_cup_graph_agent.infrastructure.secrets.credentials import (
    Principal,
    get_openrouter_api_key,
)


logger = structlog.get_logger(__name__)


# Scenarios supply {"messages": [...]} to send a user message, or
# Command(resume={"decisions": [...]}) to answer a pending approval.
type ConversationRequest = dict[str, Any] | Command[Any]

# LangGraph returns agent state, not just answer text. Evals read "messages"
# (conversation history) and optional "__interrupt__" (pending approvals).
type ConversationState = dict[str, Any]

# The conversation_runner fixture binds resources to invoke_conversation,
# leaving scenarios to supply a request and LangGraph execution configuration.
type ConversationRunner = Callable[
    [ConversationRequest, RunnableConfig], Awaitable[ConversationState]
]


async def invoke_conversation(
    settings: EvalSettings,
    drivers: GraphDrivers,
    checkpointer: AsyncNeo4jSaver,
    conversation_request: ConversationRequest,
    config: RunnableConfig,
) -> ConversationState:
    scenario = config["metadata"]["eval_scenario"]
    request_kind = (
        "approval_resume" if isinstance(conversation_request, Command) else "message"
    )

    logger.info(
        "eval_conversation_started", scenario=scenario, request_kind=request_kind
    )

    # Each invocation, including resumes, resolves the trusted demo identity
    # and builds fresh models, matching the local execution boundary.
    openrouter_api_key = await get_openrouter_api_key(Principal("alice"), settings)
    agent = create_app_agent(settings, checkpointer, drivers, openrouter_api_key)
    conversation_state = await agent.ainvoke(conversation_request, config)

    logger.info(
        "eval_conversation_completed",
        scenario=scenario,
        request_kind=request_kind,
        interrupts=len(conversation_state.get("__interrupt__", [])),
    )

    return conversation_state


def build_eval_config(
    settings: EvalSettings,
    family: str,
    scenario: str,
) -> RunnableConfig:
    # Resumes keep the same thread and metadata; rebuilding an agent must not
    # start a different conversation or break trace continuity.
    return RunnableConfig(
        configurable={"thread_id": scenario},
        metadata={
            **build_eval_metadata(settings),
            "thread_id": scenario,
            "tool": "eval-target",
            "eval_family": family,
            "eval_scenario": scenario,
        },
        tags=[f"eval-{family}", f"eval-{scenario}"],
    )
