from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from deepagents.middleware.subagents import CompiledSubAgent
from langchain.agents.structured_output import ToolStrategy
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from pathlib import Path
from pydantic import SecretStr
from world_cup_graph_agent.agent.completion import EnrichmentCompletion
from world_cup_graph_agent.agent.prompts import ENRICHER_PROMPT, SUPERVISOR_PROMPT
from world_cup_graph_agent.agent.research_guard import (
    EnricherResearchGuard,
    should_request_research_approval,
)
from world_cup_graph_agent.config.settings import Settings
from world_cup_graph_agent.enrichment.models import GraphEnrichmentReport
from world_cup_graph_agent.infrastructure.neo4j.driver import GraphDrivers
from world_cup_graph_agent.llm.openrouter import build_chat_model
from world_cup_graph_agent.tools.mutate_graph.tool import build_mutate_graph_tool
from world_cup_graph_agent.tools.query_graph import build_query_graph_tool
from world_cup_graph_agent.tools.research_web import build_research_web_tool


_PACKAGED_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"

_QUERYING_SKILL_SOURCE = "/skills/querying"

_ENRICHMENT_SKILL_SOURCE = "/skills/enrichment"

# Virtual mode confines paths without making skills read-only, so writes
# under the skills tree stay explicitly denied at the permission layer.
_SKILLS_PERMISSIONS: list[FilesystemPermission] = [
    FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny")
]


def create_app_agent(
    settings: Settings,
    checkpointer: Checkpointer | None,
    drivers: GraphDrivers,
    openrouter_api_key: SecretStr,
) -> CompiledStateGraph:
    """Let the LangGraph dev server manage persistence when checkpointer is None."""
    query_graph = build_query_graph_tool(settings, drivers.reader)
    research_web = build_research_web_tool(settings, openrouter_api_key)
    mutate_graph = build_mutate_graph_tool(settings, drivers.writer)

    supervisor_model = build_chat_model(
        settings.openrouter_supervisor_model_id,
        settings.openrouter_supervisor_providers,
        role="supervisor",
        openrouter_api_key=openrouter_api_key,
        reasoning_effort=settings.openrouter_supervisor_reasoning_effort,
        sort=settings.openrouter_supervisor_sort,
    )

    enricher_model = build_chat_model(
        settings.openrouter_enricher_model_id,
        settings.openrouter_enricher_providers,
        role="enricher",
        openrouter_api_key=openrouter_api_key,
        reasoning_effort=settings.openrouter_enricher_reasoning_effort,
        sort=settings.openrouter_enricher_sort,
    )

    backend = CompositeBackend(
        default=StateBackend(),
        routes={
            "/skills/": FilesystemBackend(
                root_dir=_PACKAGED_SKILLS_ROOT, virtual_mode=True
            )
        },
    )

    # The enricher is a compiled subagent sharing the application checkpointer
    # so sequential research and mutation approvals resume one enrichment run.
    # The enricher's general-purpose entry is a DISABLED stub with no tools,
    # skills, or interrupts, masking the harness default worker that would
    # inherit research_web and mutate_graph outside the refusal guard.
    graph_enricher: CompiledSubAgent = {
        "name": "graph-enricher",
        "description": (
            "Research missing World Cup facts and propose atomic graph "
            "mutations, returning orchestration status."
        ),
        "runnable": create_deep_agent(
            model=enricher_model,
            tools=[query_graph, research_web, mutate_graph],
            system_prompt=ENRICHER_PROMPT,
            skills=[_QUERYING_SKILL_SOURCE, _ENRICHMENT_SKILL_SOURCE],
            backend=backend,
            permissions=_SKILLS_PERMISSIONS,
            interrupt_on={
                "research_web": {
                    "allowed_decisions": ["approve", "reject"],
                    # A research approval card is raised only when that call
                    # can still run. Further requests after usable findings
                    # must not consume an approval step before the graph update.
                    "when": should_request_research_approval,
                },
                "mutate_graph": {"allowed_decisions": ["approve", "reject"]},
            },
            middleware=[EnricherResearchGuard(), EnrichmentCompletion()],
            subagents=[
                {
                    "name": "general-purpose",
                    "description": "DISABLED",
                    "system_prompt": "DISABLED",
                    "tools": [],
                    "skills": [],
                    "interrupt_on": {},
                }
            ],
            response_format=ToolStrategy(GraphEnrichmentReport),
            checkpointer=checkpointer,
        ),
    }

    # The bundled general-purpose worker inherits the supervisor
    # model, query_graph, and the querying skill with no research
    # or mutation capability.
    return create_deep_agent(
        model=supervisor_model,
        tools=[query_graph],
        system_prompt=SUPERVISOR_PROMPT,
        skills=[_QUERYING_SKILL_SOURCE],
        subagents=[graph_enricher],
        backend=backend,
        permissions=_SKILLS_PERMISSIONS,
        checkpointer=checkpointer,
    )
