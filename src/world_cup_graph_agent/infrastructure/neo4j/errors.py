import re
from neo4j.exceptions import Neo4jError


_MAX_MESSAGE_CHARS = 500

_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|token|authorization|bearer)(\s*[:=]\s*)\S+"
)


def find_neo4j_error(error: BaseException) -> Neo4jError | None:
    """Return the Neo4j server error buried in a wrapper chain.

    Streaming failures arrive wrapped as a result error whose cause chain
    still carries the server error, so checking only the outer exception
    loses the server code and message. Walk the chain so callers report
    the server code through mid-stream failures such as the
    transaction-memory termination.
    """
    current: BaseException | None = error

    while current is not None:
        if isinstance(current, Neo4jError):
            return current

        current = (
            current.__cause__ if current.__cause__ is not None else current.__context__
        )

    return None


def redact_message(message: str) -> str:
    # Failure text never carries the submitted Cypher or parameters, so the
    # only credential risk is a server message echoing secret-like text.
    redacted = _CREDENTIAL_PATTERN.sub(r"\1\2[redacted]", message)

    return redacted[:_MAX_MESSAGE_CHARS]
