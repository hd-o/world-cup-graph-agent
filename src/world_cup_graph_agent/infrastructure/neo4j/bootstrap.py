import asyncio
from aiomisc import timeout
from neo4j import AsyncDriver, AsyncSession
from neo4j.exceptions import ClientError
from typing import LiteralString, cast
from world_cup_graph_agent.config.settings import Settings
from world_cup_graph_agent.graph.schema.nodes import (
    list_node_labels,
    list_node_property_keys,
)
from world_cup_graph_agent.graph.schema.relationships import (
    list_relationship_id_types,
    list_relationship_property_keys,
    list_relationship_types,
)
from world_cup_graph_agent.graph.schema.serializer import write_serialized_schema
from world_cup_graph_agent.graph.seed.seeder import SeedOutcome, seed_graph
from world_cup_graph_agent.infrastructure.neo4j.checkpoints import CheckpointSaver
from world_cup_graph_agent.infrastructure.neo4j.conversations import (
    create_conversation_constraints,
)
from world_cup_graph_agent.infrastructure.neo4j.driver import (
    acquire_bootstrap_driver,
    create_writer_driver,
)


async def run_bootstrap(settings: Settings) -> None:

    async with acquire_bootstrap_driver(settings) as admin:
        await _verify_admin_connection(admin)
        print(f"Bootstrap connected to {settings.neo4j_uri}")

        await _ensure_database_online(admin, settings.neo4j_database)
        print(f"Database {settings.neo4j_database} is online")

        await _ensure_database_online(admin, settings.neo4j_checkpoint_database)
        await _ensure_checkpoint_permissions(admin, settings)
        await CheckpointSaver(
            admin, database=settings.neo4j_checkpoint_database
        ).setup()
        await create_conversation_constraints(admin, settings.neo4j_checkpoint_database)
        print(f"Checkpoint database {settings.neo4j_checkpoint_database} is ready")

        await _apply_transaction_memory_bound(admin)
        print("Transaction memory bound is 64MiB")

        await _ensure_runtime_users(admin, settings)
        print("Reader and writer users hold the reader and editor roles")

        await _ensure_runtime_denies(admin)
        print("LOAD and procedure execution stay denied on reader and editor")

        await _ensure_uniqueness_constraints(admin, settings)
        print("Uniqueness constraints are installed")

        await _ensure_token_names(admin, settings)
        print("Property, label, and relationship tokens are pre-created")

    # Rendering precedes seeding so the packaged reference always matches
    # the registries the constraints and tokens were derived from.
    reference_path = write_serialized_schema()
    print(f"Schema reference rendered to {reference_path}")


async def main(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    await run_bootstrap(settings)
    writer = create_writer_driver(settings)

    try:
        outcome = await seed_graph(writer, settings)
    finally:
        await writer.close()

    if outcome == SeedOutcome.SEEDED:
        print("Seed loaded from assets/data/seed/")
    else:
        print("Graph already contains nodes; seed left unchanged")


@timeout(30.0)
async def _verify_admin_connection(admin: AsyncDriver) -> None:
    # The driver connects lazily, so the first statement carries the
    # connection timeout; verifying here fails fast before any mutation.
    await admin.verify_connectivity()


@timeout(120)
async def _ensure_database_online(admin: AsyncDriver, name: str) -> None:
    database = _quote_name(name)

    async with admin.session(database="system") as session:
        await _execute(session, f"CREATE DATABASE {database} IF NOT EXISTS", {})

        while True:
            rows = await _execute(
                session,
                f"SHOW DATABASE {database} YIELD currentStatus "
                "RETURN currentStatus AS status",
                {},
            )

            if rows and rows[0].get("status") == "online":
                return

            await asyncio.sleep(2)


@timeout(60)
async def _apply_transaction_memory_bound(admin: AsyncDriver) -> None:
    # The 64MiB bound stays a literal in this statement so development and
    # eval services receive the same bound from this code path without a
    # Settings field or environment variable. Runtime changes through
    # dbms.setConfigValue are not persisted, so bootstrap is rerun after
    # each server restart.
    async with admin.session(database="system") as session:
        await _execute(
            session,
            "CALL dbms.setConfigValue('db.memory.transaction.max', '64MiB')",
            {},
        )


_IDENTITY_TIMEOUT_SECONDS = 60.0


@timeout(_IDENTITY_TIMEOUT_SECONDS)
async def _ensure_runtime_users(admin: AsyncDriver, settings: Settings) -> None:
    # Only built-in roles are used; the editor role keeps its broader write
    # authority including delete, and no name-management privilege is granted.
    await _ensure_user(
        admin,
        settings.neo4j_reader_username,
        settings.neo4j_reader_password.get_secret_value(),
        "reader",
    )

    await _ensure_user(
        admin,
        settings.neo4j_writer_username,
        settings.neo4j_writer_password.get_secret_value(),
        "editor",
    )


async def _ensure_user(
    admin: AsyncDriver, username: str, password: str, role: str
) -> None:
    quoted_username = _quote_name(username)

    async with admin.session(database="system") as session:
        await _execute(
            session,
            f"CREATE USER {quoted_username} IF NOT EXISTS "
            "SET PASSWORD $password CHANGE NOT REQUIRED",
            {"password": password},
        )

        try:
            await _execute(
                session,
                f"ALTER USER {quoted_username} "
                "SET PASSWORD $password CHANGE NOT REQUIRED",
                {"password": password},
            )
        except ClientError as error:
            if not _reports_same_password(error):
                raise

        # Granting an already-held role succeeds, so reruns stay idempotent.
        await _execute(
            session, f"GRANT ROLE {_quote_name(role)} TO {quoted_username}", {}
        )


@timeout(_IDENTITY_TIMEOUT_SECONDS)
async def _ensure_checkpoint_permissions(
    admin: AsyncDriver, settings: Settings
) -> None:
    database = _quote_name(settings.neo4j_checkpoint_database)
    statements = [
        # A deny overrides grants, so bootstrap must ensure editor access
        # is not denied before granting checkpoint access.
        f"REVOKE DENY ACCESS ON DATABASE {database} FROM editor",
        f"GRANT ACCESS ON DATABASE {database} TO editor",
        # The saver owns its labels/properties/relationships. Token creation
        # stays scoped to checkpoints without granting index/constraint DDL.
        f"GRANT NAME MANAGEMENT ON DATABASE {database} TO editor",
        # Read-only graph tools cannot access conversation state.
        f"DENY ACCESS ON DATABASE {database} TO reader",
    ]

    async with admin.session(database="system") as session:
        for statement in statements:
            await _execute(session, statement, {})


def _reports_same_password(error: ClientError) -> bool:
    # The server rejects a password change to the identical value, which is
    # exactly the steady state of a repeated bootstrap with unchanged secrets.
    return "cannot be the same" in str(error)


@timeout(_IDENTITY_TIMEOUT_SECONDS)
async def _ensure_runtime_denies(admin: AsyncDriver) -> None:
    # A deny overrides the PUBLIC grant for role holders. The bootstrap admin
    # keeps both privileges so token-creation procedures still run, and
    # function execution is never denied so built-in Cypher functions stay
    # available. Repeating a deny succeeds, so reruns leave them in place.
    statements = [
        "DENY LOAD ON ALL DATA TO reader",
        "DENY EXECUTE PROCEDURE * ON DBMS TO reader",
        "DENY LOAD ON ALL DATA TO editor",
        "DENY EXECUTE PROCEDURE * ON DBMS TO editor",
    ]

    async with admin.session(database="system") as session:
        for statement in statements:
            await _execute(session, statement, {})


_SCHEMA_TIMEOUT_SECONDS = 120


@timeout(_SCHEMA_TIMEOUT_SECONDS)
async def _ensure_uniqueness_constraints(
    admin: AsyncDriver, settings: Settings
) -> None:
    statements = [
        (
            f"CREATE CONSTRAINT {_quote_name(f'{label.casefold()}_id_unique')} "
            f"IF NOT EXISTS FOR (node:{_quote_name(label)}) "
            "REQUIRE node.id IS UNIQUE"
        )
        for label in list_node_labels()
    ]

    statements += [
        (
            f"CREATE CONSTRAINT {_quote_name(f'{rel_type.casefold()}_id_unique')} "
            f"IF NOT EXISTS FOR ()-[relationship:{_quote_name(rel_type)}]-() "
            "REQUIRE relationship.id IS UNIQUE"
        )
        for rel_type in list_relationship_id_types()
    ]

    async with admin.session(database=settings.neo4j_database) as session:
        for statement in statements:
            await _execute(session, statement, {})


@timeout(_SCHEMA_TIMEOUT_SECONDS)
async def _ensure_token_names(admin: AsyncDriver, settings: Settings) -> None:
    # The editor role cannot create property, label, or relationship tokens,
    # so every declared name is pre-created here under the bootstrap identity,
    # including optional properties absent from the seed.
    declared_properties = sorted(
        set(list_node_property_keys()) | set(list_relationship_property_keys())
    )

    declared_labels = sorted(list_node_labels())
    declared_types = sorted(list_relationship_types())

    async with admin.session(database=settings.neo4j_database) as session:
        await _ensure_missing_tokens(
            session,
            declared_properties,
            "CALL db.propertyKeys() YIELD propertyKey RETURN propertyKey AS name",
            "CALL db.createProperty($name)",
        )

        await _ensure_missing_tokens(
            session,
            declared_labels,
            "CALL db.labels() YIELD label RETURN label AS name",
            "CALL db.createLabel($name)",
        )

        await _ensure_missing_tokens(
            session,
            declared_types,
            "CALL db.relationshipTypes() YIELD relationshipType "
            "RETURN relationshipType AS name",
            "CALL db.createRelationshipType($name)",
        )


async def _ensure_missing_tokens(
    session: AsyncSession,
    declared: list[str],
    list_statement: str,
    create_statement: str,
) -> None:
    rows = await _execute(session, list_statement, {})
    existing = {row["name"] for row in rows if isinstance(row.get("name"), str)}

    for name in declared:
        if name not in existing:
            await _execute(session, create_statement, {"name": name})


async def _execute(
    session: AsyncSession, statement: str, parameters: dict[str, object]
) -> list[dict[str, object]]:
    # The driver types its query argument as LiteralString; bootstrap
    # statements embed validated registry and operator names, and this cast
    # erases at runtime.
    result = await session.run(cast(LiteralString, statement), parameters)

    return await result.data()


def _quote_name(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


if __name__ == "__main__":
    asyncio.run(main())
