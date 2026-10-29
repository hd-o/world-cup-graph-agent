from world_cup_graph_agent.config.settings import Settings


def build_trace_metadata(
    settings: Settings,
    environment: str,
    runtime: str = "local",
    thread_id: str | None = None,
    tool: str | None = None,
) -> dict[str, str]:
    """Build allowlisted LangSmith metadata for one invocation.
    Only safe routing fields are emitted: environment, app version, runtime,
    the model id and reasoning effort of each agent role, and optional thread
    and tool identifiers. The model fields sit on the root run, so traces filter
    and group by model configuration without opening a child run.
    Secrets never enter metadata.
    """
    metadata: dict[str, str] = {
        "environment": environment,
        "app_version": settings.app_version,
        "runtime": runtime,
        "supervisor_model": settings.openrouter_supervisor_model_id,
        "supervisor_reasoning_effort": settings.openrouter_supervisor_reasoning_effort
        or "default",
        "enricher_model": settings.openrouter_enricher_model_id,
        "enricher_reasoning_effort": settings.openrouter_enricher_reasoning_effort
        or "default",
        "research_model": settings.openrouter_research_model_id,
        "research_reasoning_effort": settings.openrouter_research_reasoning_effort
        or "default",
    }

    optional: dict[str, str | None] = {
        "thread_id": thread_id,
        "tool": tool,
    }

    for key, value in optional.items():
        if value is not None:
            metadata[key] = value

    return metadata
