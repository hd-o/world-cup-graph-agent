from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import cache
from neo4j import AsyncDriver, AsyncGraphDatabase, GraphDatabase, TrustCustomCAs
from ssl import create_default_context
from typing import Any
from world_cup_graph_agent.config.settings import Settings


def create_writer_driver(settings: Settings) -> AsyncDriver:
    """Build a writer-identity driver for graph mutations or checkpoints."""
    return _open_driver(
        settings,
        settings.neo4j_writer_username,
        settings.neo4j_writer_password.get_secret_value(),
    )


def verify_database_connectivity(settings: Settings) -> None:
    options: dict[str, Any] = {}
    if settings.runtime != "local":
        options["notifications_min_severity"] = "OFF"
    if settings.neo4j_encrypted:
        options |= {
            "encrypted": True,
            "trusted_certificates": TrustCustomCAs(str(settings.neo4j_ca_certificate)),
        }

    # Managed SDKs initialize outside their async serving loop. Disposable
    # synchronous probes keep serving connections lazy and loop-affine.
    for username, password in (
        (settings.neo4j_reader_username, settings.neo4j_reader_password),
        (settings.neo4j_writer_username, settings.neo4j_writer_password),
    ):
        with GraphDatabase.driver(
            settings.neo4j_uri, auth=(username, password.get_secret_value()), **options
        ) as driver:
            driver.verify_connectivity()


@dataclass(frozen=True)
class GraphDrivers:
    reader: AsyncDriver
    writer: AsyncDriver

    async def close(self) -> None:
        # A failure closing one identity must not skip the other, or the
        # process would keep an open connection it no longer tracks.
        try:
            await self.reader.close()
        finally:
            await self.writer.close()


def create_graph_drivers(settings: Settings) -> GraphDrivers:
    """Open the reader and writer drivers for one process or eval run."""
    return GraphDrivers(
        reader=_create_reader_driver(settings),
        writer=create_writer_driver(settings),
    )


@cache
def get_application_drivers() -> GraphDrivers:
    """Share connections owned by the server's checkpoint lifespan."""
    return create_graph_drivers(Settings())


@asynccontextmanager
async def acquire_bootstrap_driver(
    settings: Settings,
) -> AsyncGenerator[AsyncDriver]:
    """Open the short-lived admin driver for one bootstrap run, then close it.

    The application runtime never holds this driver; it exists only for the
    duration of the bootstrap workflow that creates the database, roles, users,
    constraints, and tokens.
    """
    driver = _open_driver(
        settings,
        settings.neo4j_bootstrap_username,
        settings.neo4j_bootstrap_password.get_secret_value(),
    )

    try:
        yield driver
    finally:
        await driver.close()


def _open_driver(settings: Settings, username: str, password: str) -> AsyncDriver:
    if not settings.neo4j_encrypted:
        return AsyncGraphDatabase.driver(settings.neo4j_uri, auth=(username, password))

    certificate = settings.neo4j_ca_certificate
    if certificate is None:
        raise ValueError("Bolt TLS requires a public CA certificate")

    # Validate the public CA before the lazy driver opens any connection. This
    # context is local to this check and does not alter HTTPS trust roots.
    create_default_context(cafile=str(certificate))

    return AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(username, password),
        encrypted=True,
        trusted_certificates=TrustCustomCAs(str(certificate)),
        # Server notifications include raw Cypher. Hosted diagnostics must not
        # duplicate private tool payloads into application logs.
        notifications_min_severity="OFF" if settings.runtime != "local" else None,
    )


def _create_reader_driver(settings: Settings) -> AsyncDriver:
    """Build the long-lived reader-identity driver used by query_graph."""
    return _open_driver(
        settings,
        settings.neo4j_reader_username,
        settings.neo4j_reader_password.get_secret_value(),
    )
