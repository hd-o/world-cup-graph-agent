from aiomisc import timeout
from langchain_core.tools import BaseTool, tool
from neo4j import AsyncDriver, AsyncTransaction
from typing import assert_never
from world_cup_graph_agent.config.settings import Settings
from world_cup_graph_agent.infrastructure.neo4j.errors import (
    find_neo4j_error,
    redact_message,
)
from world_cup_graph_agent.infrastructure.neo4j.writer import write_in_transaction
from world_cup_graph_agent.tools.mutate_graph.models import (
    MutateGraphArguments,
    MutateGraphFailure,
    MutateGraphSuccess,
    MutationBatchError,
    MutationOperation,
    MutationOutcome,
)
from world_cup_graph_agent.tools.mutate_graph.nodes import (
    run_create_node,
    run_fill_node,
)
from world_cup_graph_agent.tools.mutate_graph.relationships import (
    run_create_relationship,
    run_fill_relationship,
)


def build_mutate_graph_tool(settings: Settings, driver: AsyncDriver) -> BaseTool:
    @tool(args_schema=MutateGraphArguments)
    async def mutate_graph(
        operations: list[MutationOperation], risk_description: str
    ) -> dict[str, object]:
        """Apply one approved batch of graph mutations atomically.

        The calling enricher owns semantic decisions and approval sequencing;
        this tool owns only atomic parameterized execution and typed transport.
        One call carries the complete ordered operation list plus the batch risk
        description, and the reviewed arguments are the executed arguments with
        no second proposal representation.

        The batch runs in one writer transaction in approval order and rolls
        back completely on any failure, with no retry and no partial report.
        """
        try:

            async def _run_batch(
                transaction: AsyncTransaction,
            ) -> list[MutationOutcome]:
                return await _execute_operations(transaction, operations)

            outcomes = await write_in_transaction(driver, settings, _run_batch)

        except MutationBatchError as error:
            return MutateGraphFailure(
                status="failed",
                failed_index=error.failed_index,
                code=error.code,
                message=redact_message(error.message),
                rolled_back=True,
            ).model_dump()

        return MutateGraphSuccess(status="applied", outcomes=outcomes).model_dump(
            exclude_none=True
        )

    return mutate_graph


async def _execute_operations(
    transaction: AsyncTransaction, operations: list[MutationOperation]
) -> list[MutationOutcome]:
    # Sequential approval order matters because later operations may depend
    # on earlier ones, and one explicit transaction keeps the list atomic.
    outcomes: list[MutationOutcome] = []

    for index, flat_operation in enumerate(operations):
        try:
            outcome = await _run_operation(transaction, index, flat_operation)

        except MutationBatchError:
            raise

        except TimeoutError as error:
            raise MutationBatchError(
                index,
                "timeout",
                f"Operation exceeded the {_OPERATION_TIMEOUT_SECONDS:.0f} second bound",
            ) from error

        except Exception as error:
            raise _describe_operation_error(index, error) from error

        outcomes.append(outcome)

    return outcomes


_OPERATION_TIMEOUT_SECONDS = 120.0


# A hung operation would hold the open writer transaction. Commit
# stays outside this helper because interrupting it mid-flight
# leaves an unknown outcome.
@timeout(_OPERATION_TIMEOUT_SECONDS)
async def _run_operation(
    transaction: AsyncTransaction,
    index: int,
    flat_operation: MutationOperation,
) -> MutationOutcome:
    # Each runner revalidates its properties payload against the
    # create or fill contract registered for the target type before
    # it writes anything.
    match flat_operation.op:
        case "create_node":
            return await run_create_node(transaction, index, flat_operation)

        case "fill_node_properties":
            return await run_fill_node(transaction, index, flat_operation)

        case "create_relationship":
            return await run_create_relationship(transaction, index, flat_operation)

        case "fill_relationship_properties":
            return await run_fill_relationship(transaction, index, flat_operation)

        case _ as never:
            assert_never(never)


def _describe_operation_error(index: int, error: Exception) -> MutationBatchError:
    # The message stays raw here; the single failure handler applies the bound.
    neo4j_error = find_neo4j_error(error)

    if neo4j_error is not None:
        return MutationBatchError(index, neo4j_error.code, neo4j_error.message)

    detail = str(error) or type(error).__name__
    return MutationBatchError(index, "execution_error", detail)
