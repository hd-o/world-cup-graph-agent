from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr
from textwrap import dedent
from typing import Annotated
from world_cup_graph_agent.config.settings import Settings
from world_cup_graph_agent.llm.openrouter import build_chat_model


type ResearchScalar = str | int | float | bool | None

_MAX_QUESTION_LENGTH = 500


class ResearchWebArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=_MAX_QUESTION_LENGTH)
    reason: str = Field(min_length=1, max_length=_MAX_QUESTION_LENGTH)


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    url: HttpUrl
    title: str = Field(min_length=1, max_length=300)
    excerpt: str = Field(min_length=1, max_length=2000)


_MAX_RESEARCH_FINDINGS = 10


class ResearchFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(min_length=1, max_length=_MAX_QUESTION_LENGTH)
    answer: ResearchScalar
    explanation: str = Field(min_length=1, max_length=2000)
    sources: list[ResearchSource] = Field(min_length=1, max_length=10)


class ResearchWebResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    findings: list[ResearchFinding] = Field(max_length=_MAX_RESEARCH_FINDINGS)

    unanswered_questions: list[
        Annotated[str, Field(min_length=1, max_length=_MAX_QUESTION_LENGTH)]
    ] = Field(max_length=_MAX_RESEARCH_FINDINGS)


# Web access is OpenRouter server tools only, and these parameters bound
# every research call.
RESEARCH_SERVER_TOOLS: list[dict[str, object]] = [
    {
        "type": "openrouter:web_search",
        "parameters": {
            "engine": "exa",
            "max_results": 5,
            "max_total_results": 15,
            "max_characters": 2_000,
        },
    },
    {
        "type": "openrouter:web_fetch",
        "parameters": {
            "engine": "exa",
            "max_uses": 3,
            "max_content_tokens": 8_000,
        },
    },
]

_MAX_ATTEMPTS = 2


def build_research_web_tool(
    settings: Settings, openrouter_api_key: SecretStr
) -> BaseTool:
    """Build one research_web tool bound to the supplied settings."""

    @tool(args_schema=ResearchWebArguments)
    async def research_web(query: str, reason: str) -> dict[str, object]:
        """Research a World Cup fact gap on the live web and return sourced findings.

        The calling model owns the question and the graph reason; this tool owns
        only bounded web research with strict typed transport. Every finding
        carries at least one source, and sources are model-reported rather than
        verified citations. When both attempts fail, the result has no findings
        and lists the query as unanswered.
        """

        research_chain = build_chat_model(
            settings.openrouter_research_model_id,
            settings.openrouter_research_providers,
            role="research",
            openrouter_api_key=openrouter_api_key,
            reasoning_effort=settings.openrouter_research_reasoning_effort,
            sort=settings.openrouter_research_sort,
        ).with_structured_output(
            ResearchWebResult,
            method="json_schema",
            tools=RESEARCH_SERVER_TOOLS,
        )

        prompt = _build_research_prompt(query, reason)

        # Transport, provider, and schema failures share one repair path, and
        # neither attempt is logged: raw error text can carry credentials.
        for _ in range(_MAX_ATTEMPTS):
            try:
                parsed = await research_chain.ainvoke(prompt)

                if isinstance(parsed, ResearchWebResult):
                    return parsed.model_dump(mode="json")

                raise ValueError(
                    "Research model returned "
                    f"{type(parsed).__name__} instead of ResearchWebResult"
                )
            except Exception:
                prompt = _build_repair_prompt(query, reason)

        return ResearchWebResult(findings=[], unanswered_questions=[query]).model_dump(
            mode="json"
        )

    return research_web


def _build_repair_prompt(query: str, reason: str) -> str:
    # Raw provider/transport errors can contain credentials or response bodies.
    # A repair prompt needs only the schema, not dependency diagnostics.
    research_prompt = _build_research_prompt(query, reason)

    return dedent(f"""
        {research_prompt}
        The response was unavailable or failed structured validation.
        Return a valid response matching the requested JSON schema.
        """)


def _build_research_prompt(query: str, reason: str) -> str:
    # The endpoint does not enforce response_format when server tools are
    # present, so the prompt states the JSON shape explicitly.
    return dedent(f"""
        Research this question with web search and fetch: {query}
        Graph context for why this is needed: {reason}

        Respond with a single JSON object only, no prose or markdown fences,
        with keys "findings" and "unanswered_questions". Each finding has
        "question", "answer", "explanation", and "sources" with at least one
        source carrying "url", "title", and "excerpt". List anything
        unanswered in "unanswered_questions".
        """)
