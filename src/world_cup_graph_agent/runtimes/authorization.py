from langgraph_sdk import Auth
from typing import Any


auth = Auth()
_DEMO_TOKENS = {"demo-alice-token": "alice", "demo-bob-token": "bob"}


@auth.authenticate
async def authenticate(
    headers: dict[bytes, bytes], authorization: str | None = None
) -> Auth.types.MinimalUserDict:
    """Supply the caller identity LangGraph needs for ownership and credentials.

    Public demo tokens are local teaching fixtures, not production authentication.
    """
    # Public local fixtures stand in for an identity provider. Ownership and
    # credential selection use this identity, never a caller-supplied owner.
    # Explicit Bearer credentials take precedence over the browser UI's API key.
    credentials = authorization
    if credentials is None:
        token = headers.get(b"x-api-key", b"").decode("utf-8", errors="replace")
        credentials = f"Bearer {token}"

    scheme, _, token = credentials.partition(" ")
    identity = _DEMO_TOKENS.get(token) if scheme.lower() == "bearer" else None

    if identity is None:
        raise Auth.exceptions.HTTPException(401, "Unauthorized")

    return {"identity": identity}


@auth.on
async def deny_access(ctx: Auth.types.AuthContext, value: Any) -> bool:
    """Keep unapproved operations outside the tutorial's API scope.

    Applies to authorization hooks, not unhooked administrative endpoints.
    """
    return False


@auth.on.threads.create
async def create_conversation(
    ctx: Auth.types.AuthContext, value: Auth.types.ThreadsCreate
) -> Auth.types.FilterType:
    """Assign ownership because LangGraph does not define our owner metadata.

    Override client-supplied owners to prevent impersonating another user.
    """
    value.setdefault("metadata", {})["owner"] = ctx.user.identity
    return {"owner": ctx.user.identity}


@auth.on(resources=["threads"], actions=["read", "search"])
async def read_conversations(
    ctx: Auth.types.AuthContext, value: Any
) -> Auth.types.FilterType:
    """Supply the owner filter that keeps conversation reads and searches private."""
    return {"owner": ctx.user.identity}


@auth.on.threads.create_run
async def execute_conversation(
    ctx: Auth.types.AuthContext, value: Auth.types.RunsCreate
) -> Auth.types.FilterType | bool:
    """Restrict LangGraph's broader run API to owned chat and approval resumes.

    Reject implicit thread creation, run interruption, subgraph selection,
    and arbitrary state/node commands to protect conversation continuity.
    """
    if (
        not value.get("thread_id")
        or value.get("if_not_exists") != "reject"
        or value.get("multitask_strategy") not in ("reject", "enqueue")
    ):
        return False

    execution = value.get("kwargs") or {}
    configuration = (execution.get("config") or {}).get("configurable") or {}

    if "checkpoint_map" in configuration or configuration.get("checkpoint_ns"):
        return False

    # Agent Chat includes a root checkpoint on subsequent turns. Always
    # continue current state, never branch or replay a client-selected snapshot.
    configuration.pop("checkpoint_id", None)

    command = execution.get("command")
    input = execution.get("input")

    # Chat and approval cannot inject arbitrary state or redirect graph nodes.
    if command is not None:
        if input not in (None, {}) or set(command) != {"resume"}:
            return False
        # Agent Chat submits an empty input alongside approval commands.
        execution["input"] = None
    elif (
        not isinstance(input, dict)
        or set(input) != {"messages"}
        or not isinstance(input["messages"], list)
        or not input["messages"]
    ):
        return False
    else:
        messages = [parse_user_message(message) for message in input["messages"]]
        if any(message is None for message in messages):
            return False
        execution["input"] = {"messages": messages}

    return {"owner": ctx.user.identity}


def parse_user_message(message: Any) -> dict[str, str] | None:
    """Accept SDK text and Agent Chat text blocks without client state edits."""
    if not isinstance(message, dict):
        return None

    if set(message) == {"role", "content"}:
        if message["role"] not in ("user", "human"):
            return None
    elif set(message) in ({"type", "content"}, {"id", "type", "content"}):
        if message["type"] != "human":
            return None
    else:
        return None

    content = message["content"]
    if isinstance(content, list):
        if not content or not all(
            isinstance(block, dict)
            and set(block) == {"type", "text"}
            and block["type"] == "text"
            and isinstance(block["text"], str)
            for block in content
        ):
            return None
        content = "\n".join(block["text"] for block in content)

    if not isinstance(content, str) or not content.strip():
        return None

    # LangGraph merges messages by ID; client IDs must not overwrite history.
    return {"role": "user", "content": content}


@auth.on.assistants.read
async def read_assistant(
    ctx: Auth.types.AuthContext, value: Auth.types.AssistantsRead
) -> bool:
    """Permit SDK and Studio introspection of the shared assistant.

    This exception grants neither private conversation access nor modification.
    """
    return True
