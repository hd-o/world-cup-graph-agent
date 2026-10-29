from neo4j import AsyncTransaction
from typing import LiteralString, cast
from world_cup_graph_agent.graph.schema.nodes import NODES, NodeType
from world_cup_graph_agent.tools.mutate_graph.models import (
    MutationBatchError,
    MutationOperation,
    MutationOutcome,
    MutationOutcomeState,
)
from world_cup_graph_agent.tools.mutate_graph.validation import (
    parse_payload,
    require_fields,
)


async def run_create_node(
    transaction: AsyncTransaction, index: int, flat_operation: MutationOperation
) -> MutationOutcome:
    require_fields(
        index,
        flat_operation,
        present=["node_type", "properties"],
        absent=["relationship_type", "id", "start_id", "end_id"],
        message="create_node requires node_type and properties only",
    )

    node_type = NodeType(cast(str, flat_operation.node_type))
    node_entry = NODES[node_type]
    create_model = node_entry.create

    if create_model is None:
        raise MutationBatchError(
            index, "validation_error", f"Node type {node_entry.label} is not creatable"
        )

    create_instance = parse_payload(index, create_model, flat_operation.properties)

    if node_entry.build_id is None:
        raise MutationBatchError(
            index, "validation_error", f"Node type {node_entry.label} is not creatable"
        )

    node_id = node_entry.build_id(create_instance)

    persisted: dict[str, object] = create_instance.model_dump(exclude_none=True)

    persisted["id"] = node_id

    label = node_entry.label

    statement = cast(
        LiteralString, f"CREATE (created:{label} $props) RETURN created.id AS target_id"
    )

    result = await transaction.run(statement, {"props": persisted})
    rows = await result.data()

    if not rows:
        raise MutationBatchError(index, "not_found", "Node creation returned no rows")

    return MutationOutcome(
        index=index,
        operation="create_node",
        result=MutationOutcomeState.CREATED,
        target_id=node_id,
    )


async def run_fill_node(
    transaction: AsyncTransaction, index: int, flat_operation: MutationOperation
) -> MutationOutcome:
    require_fields(
        index,
        flat_operation,
        present=["node_type", "id", "properties"],
        absent=["relationship_type", "start_id", "end_id"],
        message="fill_node_properties requires node_type, id, and properties only",
    )

    target_id = cast(str, flat_operation.id)

    if not target_id.strip() or not flat_operation.properties:
        raise MutationBatchError(
            index,
            "validation_error",
            "fill_node_properties requires a target and a value",
        )

    node_type = NodeType(cast(str, flat_operation.node_type))
    node_entry = NODES[node_type]

    if node_entry.fill is None:
        raise MutationBatchError(
            index,
            "validation_error",
            f"Node type {node_entry.label} has no fillable properties",
        )

    fill_instance = parse_payload(index, node_entry.fill, flat_operation.properties)

    supplied: dict[str, object] = fill_instance.model_dump(exclude_none=True)

    if not supplied:
        raise MutationBatchError(
            index, "validation_error", "fill_node_properties requires a value"
        )

    # Fill models exclude id and identity by construction, and every supplied
    # property must currently be missing, so an equal-value replay still fails.
    label = node_entry.label
    null_checks = " AND ".join(f"target.{name} IS NULL" for name in supplied)

    statement = cast(
        LiteralString,
        f"MATCH (target:{label} {{id: $node_id}}) "
        f"WHERE {null_checks} "
        "SET target += $fill_props "
        "RETURN target.id AS target_id",
    )

    result = await transaction.run(
        statement, {"node_id": target_id, "fill_props": supplied}
    )

    rows = await result.data()

    if not rows:
        raise MutationBatchError(
            index,
            "already_populated",
            "Node fill precondition failed: node is missing or a property is populated",
        )

    return MutationOutcome(
        index=index,
        operation="fill_node_properties",
        result=MutationOutcomeState.CREATED,
    )
