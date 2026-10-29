from evals.settings import EvalSettings
from world_cup_graph_agent.observability.metadata import build_trace_metadata


def build_eval_metadata(settings: EvalSettings) -> dict[str, str]:
    """Describe the model configuration shared by experiments and scenarios."""
    return {
        **build_trace_metadata(settings, settings.environment),
        "judge_model": settings.openrouter_judge_model_id,
        "judge_providers": (
            ",".join(settings.openrouter_judge_providers)
            if settings.openrouter_judge_providers
            else settings.openrouter_judge_sort
        ),
    }
