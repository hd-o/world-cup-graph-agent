import json
from aiomisc import timeout
from enum import StrEnum
from neo4j import AsyncDriver, AsyncTransaction
from pathlib import Path
from typing import LiteralString, cast
from world_cup_graph_agent.config.settings import Settings
from world_cup_graph_agent.graph.schema.nodes import NODES
from world_cup_graph_agent.graph.schema.relationships import RELATIONSHIPS
from world_cup_graph_agent.infrastructure.neo4j.writer import write_in_transaction


class SeedOutcome(StrEnum):
    SEEDED = "seeded"
    ALREADY_POPULATED = "already_populated"


@timeout(600)
async def seed_graph(driver: AsyncDriver, settings: Settings) -> SeedOutcome:
    """Seed an empty graph from the JSON snapshot or leave it unchanged."""

    async def _write_seed(transaction: AsyncTransaction) -> SeedOutcome:
        # A non-empty graph is never reseeded, so the count check and the
        # seed writes share one writer transaction and stay atomic.
        if await _is_db_populated(transaction):
            return SeedOutcome.ALREADY_POPULATED

        for statement, rows in _load_seed_batches():
            # CREATE is safe only after the empty-graph check in
            # this same transaction; every row travels as a parameter.
            seed_result = await transaction.run(
                cast(LiteralString, statement),
                {"rows": rows},
            )
            await seed_result.consume()

        return SeedOutcome.SEEDED

    return await write_in_transaction(driver, settings, _write_seed)


async def _is_db_populated(transaction: AsyncTransaction) -> bool:
    """Return True when the graph already holds at least one node."""
    count_result = await transaction.run(
        "MATCH (node) RETURN count(node) AS node_count",
        {},
    )
    count_rows = await count_result.data()
    return bool(count_rows) and count_rows[0]["node_count"] > 0


def _load_seed_batches() -> list[tuple[str, list[dict[str, object]]]]:
    """Build parameterized batches in node-then-relationship order."""
    seed_root = _resolve_seed_path()
    batches: list[tuple[str, list[dict[str, object]]]] = []

    for entry in NODES.values():
        statement = f"UNWIND $rows AS row CREATE (node:{entry.label}) SET node += row"
        rows = _read_seed_rows(seed_root / "nodes" / f"{entry.label}.json")
        batches.append((statement, rows))

    for entry in RELATIONSHIPS.values():
        start_label = NODES[entry.start_label].label
        end_label = NODES[entry.end_label].label
        relationship_type = entry.relationship_type.value
        statement = (
            "UNWIND $rows AS row "
            f"MATCH (start:{start_label} {{id: row.start_id}}) "
            f"MATCH (end:{end_label} {{id: row.end_id}}) "
            f"CREATE (start)-[rel:{relationship_type}]->(end)"
        )

        if entry.create is not None:
            statement += " SET rel += row.properties"

        rows = _read_seed_rows(
            seed_root / "relationships" / f"{relationship_type}.json"
        )

        batches.append((statement, rows))

    return batches


def _read_seed_rows(path: Path) -> list[dict[str, object]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_seed_path() -> Path:
    # Resolve from __file__ so bootstrap and evals do not depend on cwd.
    repository_root = Path(__file__).resolve().parents[4]
    return repository_root / "assets" / "data" / "seed"
