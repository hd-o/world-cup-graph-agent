from neo4j import AsyncTransaction
from typing import LiteralString, cast
from world_cup_graph_agent.graph.schema.nodes import NODES
from world_cup_graph_agent.graph.schema.relationships import (
    RELATIONSHIPS,
    PlaysForCreation,
    PlaysForFill,
    RelationshipEntry,
    RelationshipType,
)
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


async def run_create_relationship(
    transaction: AsyncTransaction, index: int, flat_operation: MutationOperation
) -> MutationOutcome:
    require_fields(
        index,
        flat_operation,
        present=["relationship_type", "start_id", "end_id"],
        absent=["node_type", "id"],
        message="create_relationship requires relationship_type, start_id, and end_id",
    )

    start_id = cast(str, flat_operation.start_id)
    end_id = cast(str, flat_operation.end_id)

    if not start_id.strip() or not end_id.strip():
        raise MutationBatchError(
            index, "validation_error", "create_relationship requires endpoint ids"
        )

    relationship_entry = RELATIONSHIPS[
        cast(RelationshipType, flat_operation.relationship_type)
    ]

    if relationship_entry.build_id is not None:
        return await _run_create_plays_for(
            transaction, index, flat_operation, relationship_entry
        )

    if flat_operation.properties is not None:
        raise MutationBatchError(
            index,
            "validation_error",
            "Propertyless relationship creation carries no properties",
        )

    start_label = NODES[relationship_entry.start_label].label
    end_label = NODES[relationship_entry.end_label].label
    relationship_name = relationship_entry.relationship_type.value

    await _require_endpoint(transaction, index, start_label, start_id)
    await _require_endpoint(transaction, index, end_label, end_id)

    pair_count = await _count_propertyless_pair(
        transaction,
        start_label,
        end_label,
        relationship_name,
        start_id,
        end_id,
    )

    if pair_count > 1:
        raise MutationBatchError(
            index, "conflict", "More than one relationship already links the pair"
        )

    # MERGE of only the relationship keeps idempotent creation; no SET follows
    # because propertyless types carry no properties.
    merge_statement = cast(
        LiteralString,
        f"MATCH (start:{start_label} {{id: $start_id}}), "
        f"(end:{end_label} {{id: $end_id}}) "
        f"MERGE (start)-[rel:{relationship_name}]->(end)",
    )

    merge_result = await transaction.run(
        merge_statement,
        {"start_id": start_id, "end_id": end_id},
    )

    await merge_result.consume()

    if pair_count == 1:
        return MutationOutcome(
            index=index,
            operation="create_relationship",
            result=MutationOutcomeState.ALREADY_PRESENT,
        )

    return MutationOutcome(
        index=index,
        operation="create_relationship",
        result=MutationOutcomeState.CREATED,
    )


async def run_fill_relationship(
    transaction: AsyncTransaction, index: int, flat_operation: MutationOperation
) -> MutationOutcome:
    require_fields(
        index,
        flat_operation,
        present=["relationship_type", "id", "properties"],
        absent=["node_type", "start_id", "end_id"],
        message="fill_relationship_properties requires type, id, and properties",
    )

    target_id = cast(str, flat_operation.id)

    if flat_operation.relationship_type != RelationshipType.PLAYS_FOR:
        raise MutationBatchError(
            index, "validation_error", "Only PLAYS_FOR supports property fills"
        )

    if not target_id.strip() or not flat_operation.properties:
        raise MutationBatchError(
            index,
            "validation_error",
            "fill_relationship_properties requires a target and a value",
        )

    fill_instance = parse_payload(index, PlaysForFill, flat_operation.properties)

    supplied: dict[str, object] = fill_instance.model_dump(exclude_none=True)

    if not supplied:
        raise MutationBatchError(
            index, "validation_error", "fill_relationship_properties requires a value"
        )

    relationship_name = RelationshipType.PLAYS_FOR.value
    null_checks = " AND ".join(f"target.{name} IS NULL" for name in supplied)

    statement = cast(
        LiteralString,
        f"MATCH ()-[target:{relationship_name} {{id: $rel_id}}]->() "
        f"WHERE {null_checks} "
        "SET target += $fill_props "
        "RETURN target.id AS target_id",
    )

    result = await transaction.run(
        statement, {"rel_id": target_id, "fill_props": supplied}
    )

    rows = await result.data()

    if not rows:
        raise MutationBatchError(
            index,
            "already_populated",
            "Relationship fill precondition failed: "
            "relationship is missing or a property is populated",
        )

    return MutationOutcome(
        index=index,
        operation="fill_relationship_properties",
        result=MutationOutcomeState.CREATED,
    )


async def _run_create_plays_for(
    transaction: AsyncTransaction,
    index: int,
    flat_operation: MutationOperation,
    relationship_entry: RelationshipEntry,
) -> MutationOutcome:
    assert relationship_entry.build_id is not None

    if flat_operation.properties is None:
        raise MutationBatchError(
            index, "validation_error", "PLAYS_FOR creation requires properties"
        )

    if flat_operation.start_id is None or flat_operation.end_id is None:
        raise MutationBatchError(
            index, "validation_error", "PLAYS_FOR creation requires endpoint ids"
        )

    create_instance = parse_payload(index, PlaysForCreation, flat_operation.properties)

    relationship_id = relationship_entry.build_id(
        flat_operation.start_id,
        flat_operation.end_id,
        create_instance.competition_id,
    )

    start_label = NODES[relationship_entry.start_label].label
    end_label = NODES[relationship_entry.end_label].label
    relationship_name = relationship_entry.relationship_type.value

    await _require_endpoint(transaction, index, start_label, flat_operation.start_id)
    await _require_endpoint(transaction, index, end_label, flat_operation.end_id)

    persisted: dict[str, object] = {
        "id": relationship_id,
        **create_instance.model_dump(exclude_none=True),
    }

    # CREATE never MERGE, so a duplicate deterministic id fails through the
    # uniqueness constraint and rolls the batch back.
    statement = cast(
        LiteralString,
        f"MATCH (start:{start_label} {{id: $start_id}}), "
        f"(end:{end_label} {{id: $end_id}}) "
        f"CREATE (start)-[rel:{relationship_name} $rel_props]->(end) "
        "RETURN rel.id AS target_id",
    )

    result = await transaction.run(
        statement,
        {
            "start_id": flat_operation.start_id,
            "end_id": flat_operation.end_id,
            "rel_props": persisted,
        },
    )
    rows = await result.data()

    if not rows:
        raise MutationBatchError(
            index, "not_found", "PLAYS_FOR creation found no endpoints"
        )

    return MutationOutcome(
        index=index,
        operation="create_relationship",
        result=MutationOutcomeState.CREATED,
        target_id=relationship_id,
    )


async def _require_endpoint(
    transaction: AsyncTransaction, index: int, label: str, endpoint_id: str
) -> None:
    # Endpoint labels come from the registry entry, never from wire text, and
    # the id travels as a parameter.
    statement = cast(
        LiteralString,
        f"MATCH (endpoint:{label} {{id: $endpoint_id}}) RETURN 1 AS found",
    )

    result = await transaction.run(statement, {"endpoint_id": endpoint_id})
    rows = await result.data()

    if not rows:
        raise MutationBatchError(
            index, "not_found", f"Relationship endpoint {label} is missing"
        )


async def _count_propertyless_pair(
    transaction: AsyncTransaction,
    start_label: str,
    end_label: str,
    relationship_name: str,
    start_id: str,
    end_id: str,
) -> int:
    statement = cast(
        LiteralString,
        f"MATCH (start:{start_label} {{id: $start_id}})"
        f"-[existing:{relationship_name}]->"
        f"(end:{end_label} {{id: $end_id}}) "
        "RETURN count(existing) AS pair_count",
    )

    result = await transaction.run(statement, {"start_id": start_id, "end_id": end_id})
    rows = await result.data()

    return rows[0]["pair_count"] if rows else 0
