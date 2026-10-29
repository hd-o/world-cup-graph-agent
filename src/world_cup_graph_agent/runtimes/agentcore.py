import asyncio
from bedrock_agentcore import BedrockAgentCoreApp, RequestContext
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pydantic import ValidationError
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from typing import Any
from world_cup_graph_agent.infrastructure.neo4j.driver import (
    create_graph_drivers,
    verify_database_connectivity,
)
from world_cup_graph_agent.infrastructure.secrets.managed import UserSecrets
from world_cup_graph_agent.runtimes.configuration import load_hosted_settings
from world_cup_graph_agent.runtimes.contracts import (
    create_error_response,
    request_adapter,
)
from world_cup_graph_agent.runtimes.identity import CognitoVerifier


@asynccontextmanager
async def open_application(application: BedrockAgentCoreApp) -> AsyncIterator[None]:
    from world_cup_graph_agent.observability.telemetry import configure_cloud_telemetry

    configure_cloud_telemetry("aws")

    from world_cup_graph_agent.runtimes.hosted import HostedApplication

    settings = load_hosted_settings("aws")
    await asyncio.to_thread(verify_database_connectivity, settings)
    drivers = create_graph_drivers(settings)
    secrets = UserSecrets("aws", settings.deployment_name)
    application.state.hosted = HostedApplication(
        settings, drivers, secrets.get_openrouter_api_key
    )
    application.state.verifier = CognitoVerifier(
        settings.cognito_issuer, settings.cognito_client_id
    )
    application.state.execution_loop = None
    try:
        yield
    finally:
        # AgentCore dispatches handlers on a dedicated worker loop. Close the
        # connections on their owning loop, not the ASGI lifespan's loop.
        loop = application.state.execution_loop
        if loop is None:
            await drivers.close()
        else:
            future = asyncio.run_coroutine_threadsafe(drivers.close(), loop)
            await asyncio.wrap_future(future)


class InvocationBoundary:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receiver: Receive, sender: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["path"] == "/invocations"
            and scope["method"] == "POST"
        ):
            request = Request(scope, receive=receiver)
            body = bytearray()
            try:
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 100000:
                        raise ValueError("invalid_request")
                    body.extend(chunk)
                request_adapter.validate_json(bytes(body))
            except ValidationError, ValueError:
                response = JSONResponse(
                    create_error_response("invalid_request").model_dump(mode="json"),
                )
                # AgentCore wraps non-2xx app responses as platform errors.
                # Keep the same JSON error envelope as entrypoint validation.
                await response(scope, receiver, sender)
                return

            # Forward the validated bytes once; later receives retain ASGI
            # disconnect semantics instead of duplicating the request body.
            remainder: bytes | None = bytes(body)

            async def receive_request() -> Message:
                nonlocal remainder
                if remainder is not None:
                    message: Message = {
                        "type": "http.request",
                        "body": remainder,
                        "more_body": False,
                    }
                    remainder = None
                    return message
                return await receiver()

            await self.app(scope, receive_request, sender)
            return
        await self.app(scope, receiver, sender)


# The SDK handles its reserved task-action payload before entrypoint dispatch.
# Enforce the published conversation contract before any SDK action selection.
app = BedrockAgentCoreApp(
    lifespan=open_application, middleware=[Middleware(InvocationBoundary)]
)


@app.entrypoint
async def invoke(payload: dict, context: RequestContext) -> dict[str, Any]:
    app.state.execution_loop = asyncio.get_running_loop()
    headers = {
        name.lower(): value for name, value in (context.request_headers or {}).items()
    }
    try:
        principal = await app.state.verifier.verify_token(
            headers.get("authorization", "")
        )
        request = request_adapter.validate_python(payload)
        response = await app.state.hosted.invoke(principal, request)
    except PermissionError:
        response = create_error_response("unauthenticated")
    except ValidationError:
        response = create_error_response("invalid_request")
    except Exception:
        response = create_error_response("runtime_unavailable")

    return response.model_dump(mode="json")


if __name__ == "__main__":
    app.run()
