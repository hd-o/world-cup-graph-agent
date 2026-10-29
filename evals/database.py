import structlog
from aiomisc import timeout
from evals.settings import EvalSettings
from neo4j import AsyncDriver, AsyncTransaction
from world_cup_graph_agent.graph.seed.seeder import seed_graph
from world_cup_graph_agent.infrastructure.neo4j.bootstrap import run_bootstrap
from world_cup_graph_agent.infrastructure.neo4j.writer import write_in_transaction


logger = structlog.get_logger(__name__)


async def prepare_eval_database(settings: EvalSettings) -> None:
    """Provision the isolated service using the application's database setup."""
    await run_bootstrap(settings)


@timeout(1200)
async def reset_eval_database(settings: EvalSettings, writer: AsyncDriver) -> None:
    """Give each scenario fresh graph data and persistent checkpoint storage."""
    logger.info("eval_database_reset_started", uri=settings.neo4j_uri)

    # Preserve constraints and indexes while clearing both kinds of state.
    async with writer.session(database=settings.neo4j_checkpoint_database) as session:
        result = await session.run("MATCH (node) DETACH DELETE node")
        await result.consume()

    await write_in_transaction(writer, settings, _clear_graph)
    logger.info("eval_database_seed_started")
    await seed_graph(writer, settings)
    logger.info("eval_database_ready", uri=settings.neo4j_uri)


async def _clear_graph(transaction: AsyncTransaction) -> None:
    # The runtime writer clears data without keeping an admin connection open.
    result = await transaction.run("MATCH (node) DETACH DELETE node", {})
    await result.consume()
