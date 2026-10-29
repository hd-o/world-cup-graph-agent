import pytest
from langchain_core.runnables.base import RunnableSequence
from langchain_openrouter import ChatOpenRouter
from langgraph_sdk.runtime import AccessContext, ServerRuntime
from pydantic import SecretStr
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from world_cup_graph_agent.agent import factory
from world_cup_graph_agent.config.settings import ProviderSort, Settings
from world_cup_graph_agent.infrastructure.neo4j.driver import create_graph_drivers
from world_cup_graph_agent.infrastructure.secrets import credentials
from world_cup_graph_agent.llm.openrouter import build_chat_model
from world_cup_graph_agent.runtimes import local
from world_cup_graph_agent.tools import research_web


pytestmark = [pytest.mark.credentials]


async def test_model_credentials() -> None:
    """Verify user-specific model-key wiring without inference.

    The demo's shared key can hide credential mix-ups; distinct fake keys expose
    them and confirm that assistant introspection skips credential resolution.
    """
    # Driver construction is lazy: this scenario builds real graphs and models
    # without connecting to Neo4j or sending inference requests.
    settings = Settings(openrouter_api_key=SecretStr("fixture-environment-key"))
    drivers = create_graph_drivers(settings)
    resolved_identities: list[str] = []
    model_key_records: list[tuple[str, str]] = []

    async def get_openrouter_api_key(
        principal: credentials.Principal,
        _settings: Settings,
    ) -> SecretStr:
        # Distinct fake keys reveal wiring mistakes that the shared demo key
        # would hide, such as binding Bob's models to Alice's credential.
        resolved_identities.append(principal.identity)

        return SecretStr(f"fixture-{principal.identity}-key")

    def build_model(
        model: str,
        providers: list[str],
        role: str,
        openrouter_api_key: SecretStr,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        sort: ProviderSort = "throughput",
    ) -> ChatOpenRouter:
        # Observe the real model configuration without sending an LLM request.
        chat_model = build_chat_model(
            model,
            providers,
            role,
            openrouter_api_key,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            sort=sort,
        )

        assert chat_model.openrouter_api_key is not None
        model_key_records.append(
            (role, chat_model.openrouter_api_key.get_secret_value())
        )

        return chat_model

    def build_runtime(identity: str, access_context: AccessContext) -> ServerRuntime:
        # Simulate only the trusted boundary consumed by the local factory.
        # HTTP authentication and actual server contexts belong to access evals.
        return cast(
            ServerRuntime,
            SimpleNamespace(
                ensure_user=lambda: SimpleNamespace(identity=identity),
                execution_runtime=object()
                if access_context == "threads.create_run"
                else None,
            ),
        )

    try:
        # Patch the names where callers use them, keeping real graph/model
        # construction while substituting the credential source and observers.
        with (
            patch.object(local, "settings", settings),
            patch.object(local, "get_application_drivers", return_value=drivers),
            patch.object(local, "get_openrouter_api_key", get_openrouter_api_key),
            patch.object(factory, "build_chat_model", build_model),
            patch.object(research_web, "build_chat_model", build_model),
        ):
            # Build each user's graph and verify both supervisor and enricher
            # receive that user's resolved key rather than the environment key.
            for identity in ("alice", "bob"):
                await local.create_agent(build_runtime(identity, "threads.create_run"))

            assert sorted(resolved_identities) == ["alice", "bob"]

            assert sorted(model_key_records) == sorted(
                [
                    (role, f"fixture-{owner}-key")
                    for owner in ("alice", "bob")
                    for role in ("supervisor", "enricher")
                ]
            )

            # Assistant introspection needs graph topology, not user secrets.
            # A read context must leave the credential-lookup count unchanged.
            await local.create_agent(build_runtime("alice", "assistants.read"))

            assert len(resolved_identities) == 2

            # Research builds its model when the tool is invoked, not when
            # the graph is created. Check its supplied key on that path too.
            tool = research_web.build_research_web_tool(
                settings, SecretStr("fixture-bob-key")
            )

            # Stub the model chain's response so this check needs no inference
            # or paid web research, while still exercising model construction.
            with patch.object(
                RunnableSequence,
                "ainvoke",
                new=AsyncMock(
                    return_value=research_web.ResearchWebResult(
                        findings=[], unanswered_questions=["question"]
                    )
                ),
            ):
                await tool.ainvoke({"query": "question", "reason": "gap"})

            assert model_key_records[-1] == ("research", "fixture-bob-key")

    finally:
        # Release the eval-owned drivers even if a wiring assertion fails.
        await drivers.close()
