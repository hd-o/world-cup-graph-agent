from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from langchain_neo4j import AsyncNeo4jSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph_sdk.runtime import ServerRuntime
from world_cup_graph_agent.agent.factory import create_app_agent
from world_cup_graph_agent.config.settings import Settings
from world_cup_graph_agent.infrastructure.neo4j.checkpoints import CheckpointSaver
from world_cup_graph_agent.infrastructure.neo4j.driver import get_application_drivers
from world_cup_graph_agent.infrastructure.secrets.credentials import (
    Principal,
    get_openrouter_api_key,
)
from world_cup_graph_agent.observability.logging import configure_logging


configure_logging()

settings = Settings()


async def create_agent(runtime: ServerRuntime) -> CompiledStateGraph:
    principal = Principal(runtime.ensure_user().identity)

    # Schema/history accesses need identical topology, but never resolve
    # user credentials or invoke inference. Every execution, including
    # an approval resume, owns fresh models and one resolved credential.
    openrouter_api_key = (
        await get_openrouter_api_key(principal, settings)
        if runtime.execution_runtime is not None
        else settings.openrouter_api_key
    )

    return create_app_agent(
        settings,
        checkpointer=None,
        drivers=get_application_drivers(),
        openrouter_api_key=openrouter_api_key,
    )


@asynccontextmanager
async def create_checkpointer() -> AsyncGenerator[AsyncNeo4jSaver]:
    """Supply the local LangGraph server's saver for its lifespan."""
    drivers = get_application_drivers()
    checkpoint_database = settings.neo4j_checkpoint_database

    # Run bootstrap before startup, otherwise this check fails
    async with drivers.writer.session(database=checkpoint_database) as session:
        result = await session.run("RETURN 1")
        await result.consume()

    yield CheckpointSaver(drivers.writer, database=checkpoint_database)
