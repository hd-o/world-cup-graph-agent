from langchain_openrouter import ChatOpenRouter
from pydantic import SecretStr
from world_cup_graph_agent.config.settings import ProviderSort, Settings


def build_chat_model(
    model: str,
    providers: list[str],
    role: str,
    openrouter_api_key: SecretStr,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    sort: ProviderSort = "throughput",
) -> ChatOpenRouter:
    """Build one OpenRouter chat model for the caller's role.

    Each agent role passes its own model id, provider list, and sort preference.
    Explicit provider order takes precedence; an empty list uses the sort.
    Application roles pass a typed reasoning effort and the evaluators pass
    none, so the judge keeps the provider default reasoning behavior.
    The role, provider order, and reasoning effort are flat run metadata,
    because LangSmith filters and groups on flat keys but not on the nested
    `openrouter_provider` and `reasoning` values.
    """
    return ChatOpenRouter(
        api_key=openrouter_api_key,
        app_title=Settings.app_name,
        max_tokens=20_000,
        model=model,
        openrouter_provider=(
            {
                "allow_fallbacks": False,
                "order": providers,
            }
            if providers
            else {"sort": sort}
        ),
        temperature=temperature,
        reasoning=({"effort": reasoning_effort} if reasoning_effort else None),
        tags=[f"llm-{role}"],
        metadata={
            "llm_role": role,
            "llm_providers": ",".join(providers) if providers else sort,
            "llm_reasoning_effort": reasoning_effort or "default",
        },
    )
