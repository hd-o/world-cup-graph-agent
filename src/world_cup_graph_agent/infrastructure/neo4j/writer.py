from collections.abc import Awaitable, Callable
from neo4j import AsyncDriver, AsyncTransaction
from world_cup_graph_agent.config.settings import Settings


async def write_in_transaction[T](
    driver: AsyncDriver,
    settings: Settings,
    write_operations: Callable[[AsyncTransaction], Awaitable[T]],
) -> T:
    """Run write operations inside one explicit writer transaction.

    An explicit transaction is used instead of ``execute_write`` so the driver
    never silently applies its transient-error retries to a mutation, which
    would contradict the "do not retry automatically" rule and could re-run a
    non-idempotent ``CREATE``. The caller passes each property value as a query
    parameter; nothing is interpolated into a query string here.

    ``write_operations`` must materialize every result it starts (await
    ``result.data()`` or ``result.consume()``) before returning, because commit
    discards any records still pending on the transaction. The transaction
    commits on success and rolls the whole batch back on any error, including
    cancellation, without masking the original failure.
    """
    async with (
        driver.session(database=settings.neo4j_database) as session,
        await session.begin_transaction() as transaction,
    ):
        return await write_operations(transaction)
