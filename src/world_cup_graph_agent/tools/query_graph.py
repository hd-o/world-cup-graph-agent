import json
from aiomisc import timeout
from langchain_core.tools import BaseTool, tool
from neo4j import READ_ACCESS, AsyncDriver, Query, Record
from pydantic import BaseModel, ConfigDict
from typing import Literal, LiteralString, cast
from world_cup_graph_agent.config.settings import Settings
from world_cup_graph_agent.infrastructure.neo4j.errors import (
    find_neo4j_error,
    redact_message,
)


type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
)


class QueryGraphSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["success"]
    rows: list[dict[str, JsonValue]]
    truncated: bool


class QueryGraphFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["error"]
    code: str
    message: str


_MAX_ROWS = 25
_FETCH_ROWS = _MAX_ROWS + 1
_QUERY_TIMEOUT_SECONDS = 120.0
_MAX_PAYLOAD_BYTES = 131_072

_TRANSPORT_CODE = "transport_error"
_TIMEOUT_CODE = "timeout"
_PAYLOAD_CODE = "payload_too_large"
_UNSUPPORTED_VALUE_CODE = "unsupported_value"


def build_query_graph_tool(settings: Settings, driver: AsyncDriver) -> BaseTool:
    @tool
    async def query_graph(
        cypher: str, parameters: dict[str, JsonValue]
    ) -> dict[str, object]:
        """Execute read-only Cypher against the World Cup graph and return rows.

        The calling model owns Cypher generation, query strategy, result
        interpretation, query repair, and recovery decisions; this tool owns only
        bounded read-only execution and typed transport. Cypher and parameters
        reach Neo4j unmodified, and parameters carry recursively
        JSON-compatible values only.

        Project JSON-compatible scalar properties explicitly: a driver-native
        value fails with an unsupported_value error instead of being stringified.
        At most 25 rows return; truncated is true when more rows exist. Failures
        carry a Neo4j code when available and a stable project code otherwise,
        with a bounded redacted message.
        """
        try:
            records = await _fetch_records(cypher, parameters, driver, settings)
        except TimeoutError:
            return _get_dumped_failure(
                _TIMEOUT_CODE,
                f"Query exceeded the {_QUERY_TIMEOUT_SECONDS:.0f} second bound",
            )
        except Exception as error:
            return _describe_execution_error(error)

        # _fetch_records returns one sentinel row past the row bound, so
        # truncation is decided by that sentinel's presence instead of a count.
        kept_records = records[:_MAX_ROWS]
        truncated = len(records) > _MAX_ROWS

        try:
            rows = [_convert_row(record) for record in kept_records]
        except _UnsupportedValueError as error:
            return _get_dumped_failure(_UNSUPPORTED_VALUE_CODE, str(error))

        payload_bytes = len(json.dumps(rows).encode("utf-8"))

        if payload_bytes > _MAX_PAYLOAD_BYTES:
            return _get_dumped_failure(
                _PAYLOAD_CODE,
                f"Serialized payload exceeds the "
                f"{_MAX_PAYLOAD_BYTES // 1024} KiB bound",
            )

        outcome = QueryGraphSuccess(status="success", rows=rows, truncated=truncated)

        return outcome.model_dump()

    return query_graph


@timeout(_QUERY_TIMEOUT_SECONDS)
async def _fetch_records(
    cypher: str,
    parameters: dict[str, JsonValue],
    driver: AsyncDriver,
    settings: Settings,
) -> list[Record]:
    """Run Cypher and fetch one sentinel row past the row bound.
    The statement and parameters reach Neo4j unmodified: nothing is inspected,
    rewritten, or wrapped, and Neo4j reports missing or invalid parameters.
    """
    # The driver types its query argument as LiteralString; dynamic Cypher from
    # the model cannot satisfy that annotation, and this cast erases at runtime.
    statement = cast(LiteralString, cypher)

    # Read-access mode is a routing hint only;
    # Neo4j RBAC is the read-only security boundary.
    async with driver.session(
        database=settings.neo4j_database,
        default_access_mode=READ_ACCESS,
    ) as session:
        result = await session.run(
            Query(statement, timeout=_QUERY_TIMEOUT_SECONDS),
            parameters,
        )

        records = await result.fetch(_FETCH_ROWS)
        await result.consume()
        return records


class _UnsupportedValueError(Exception):
    pass


def _convert_row(record: Record) -> dict[str, JsonValue]:
    # Raw record values keep their driver-native types. record.data() would
    # lossily convert graph and temporal values into plain collections, which
    # this tool must reject instead of silently converting.
    converted: dict[str, JsonValue] = {}

    for key, value in record.items():
        converted[key] = _to_json_value(value)

    return converted


def _to_json_value(query_value: object) -> JsonValue:
    if query_value is None or isinstance(query_value, str | bool | int | float):
        return cast(JsonValue, query_value)

    if isinstance(query_value, list):
        return [_to_json_value(item) for item in query_value]

    if isinstance(query_value, dict):
        converted_mapping: dict[str, JsonValue] = {}

        for key, item in query_value.items():
            if not isinstance(key, str):
                raise _UnsupportedValueError(
                    "Query returned a mapping with a non-string key; "
                    "project JSON-compatible scalar properties instead"
                )

            converted_mapping[key] = _to_json_value(item)

        return converted_mapping

    # Anything else is driver-native: node, relationship, path, temporal,
    # spatial, or bytes. Reject it instead of stringifying it into the row.
    raise _UnsupportedValueError(
        f"Query returned a value of type {type(query_value).__name__} "
        "with no JSON mapping; project JSON-compatible scalar properties instead"
    )


def _describe_execution_error(error: Exception) -> dict[str, object]:
    # A streaming failure arrives wrapped as a result error whose cause chain
    # still carries the server error, so the Neo4j code survives mid-stream
    # failures such as the transaction-memory termination.
    neo4j_error = find_neo4j_error(error)

    if neo4j_error is not None:
        return _get_dumped_failure(neo4j_error.code, neo4j_error.message)

    detail = str(error) or type(error).__name__
    return _get_dumped_failure(_TRANSPORT_CODE, detail)


def _get_dumped_failure(code: str, message: str) -> dict[str, object]:
    return QueryGraphFailure(
        status="error", code=code, message=redact_message(message)
    ).model_dump()
