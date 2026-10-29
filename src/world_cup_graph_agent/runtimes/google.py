import asyncio
import logging
import os
import uvicorn
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from neo4j.exceptions import ServiceUnavailable
from opentelemetry import trace
from opentelemetry.context import Context, attach, detach
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import ValidationError
from world_cup_graph_agent.infrastructure.neo4j.driver import (
    GraphDrivers,
    create_graph_drivers,
)
from world_cup_graph_agent.infrastructure.secrets.managed import UserSecrets
from world_cup_graph_agent.runtimes.configuration import load_hosted_settings
from world_cup_graph_agent.runtimes.contracts import (
    HostedInvocation,
    create_error_response,
)


logger = logging.getLogger(__name__)


async def await_database_connectivity(drivers: GraphDrivers) -> None:
    # Direct VPC networking can need several minutes to establish at startup.
    # This deadline covers probes and sleeps; application invocations never retry.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 180
    async with asyncio.timeout_at(deadline):
        while True:
            try:
                await drivers.reader.verify_connectivity()
                await drivers.writer.verify_connectivity()
                return
            except ServiceUnavailable as error:
                logger.warning(
                    "agent_database_pending error_type=%s", type(error).__name__
                )
                await asyncio.sleep(5)


def close_telemetry(provider: TracerProvider) -> None:
    try:
        provider.force_flush(timeout_millis=5000)
    finally:
        provider.shutdown()


@asynccontextmanager
async def open_application(application: FastAPI) -> AsyncIterator[None]:
    from world_cup_graph_agent.observability.telemetry import configure_cloud_telemetry

    drivers = None
    provider = None
    hosted = None
    application.state.hosted = None
    try:
        settings = await asyncio.to_thread(load_hosted_settings, "gcp")
        if settings.runtime != "local":
            configure_cloud_telemetry("gcp", settings.google_cloud_project)
            telemetry = trace.get_tracer_provider()
            if isinstance(telemetry, TracerProvider):
                provider = telemetry

        from world_cup_graph_agent.runtimes.hosted import HostedApplication

        drivers = create_graph_drivers(settings)
        await await_database_connectivity(drivers)
        secrets = UserSecrets(
            "gcp", settings.deployment_name, settings.google_cloud_project
        )
        application.state.settings = settings
        hosted = HostedApplication(settings, drivers, secrets.get_openrouter_api_key)
        application.state.hosted = hosted
        yield
    except Exception as error:
        # Library exceptions can include connection strings or request content.
        logger.error("agent_lifecycle_failed error_type=%s", type(error).__name__)
        raise RuntimeError("Agent lifecycle failed") from None
    finally:
        application.state.hosted = None
        try:
            if drivers is not None:
                await drivers.close()
        finally:
            try:
                # LangSmith queues its OTEL bridge operations separately. Drain
                # those before flushing the provider so terminal spans reach it.
                if hosted is not None:
                    await asyncio.to_thread(hosted.tracing_client.flush, timeout=5)
            finally:
                if provider is not None:
                    await asyncio.to_thread(close_telemetry, provider)


app = FastAPI(
    lifespan=open_application, docs_url=None, redoc_url=None, openapi_url=None
)


@app.get("/health")
async def read_health() -> JSONResponse:
    readiness = getattr(app.state, "hosted", None) is not None
    return JSONResponse(
        {"status": "ready" if readiness else "unavailable"},
        status_code=200 if readiness else 503,
    )


@app.post("/invoke")
async def invoke_agent(request: Request) -> JSONResponse:
    try:
        # Identity and trace metadata add to the gateway's public 100 KB budget.
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 128000:
                raise ValueError("invalid_request")
            body.extend(chunk)
        envelope = HostedInvocation.model_validate_json(bytes(body))
    except ValidationError, ValueError:
        return JSONResponse(
            create_error_response("invalid_request").model_dump(mode="json")
        )

    hosted = getattr(app.state, "hosted", None)
    if hosted is None:
        return JSONResponse(
            create_error_response("runtime_unavailable").model_dump(mode="json")
        )
    settings = app.state.settings
    principal = envelope.principal
    if (
        principal.issuer
        != f"https://securetoken.google.com/{settings.google_cloud_project}"
        or principal.tenant != settings.identity_tenant
    ):
        return JSONResponse(
            create_error_response("unauthenticated").model_dump(mode="json")
        )

    # Cloud Run invocation IAM authenticates the gateway. Only its validated W3C
    # envelope enters tracing; HTTP baggage and end-user tokens are not trusted.
    carrier = {"traceparent": envelope.traceparent} if envelope.traceparent else {}
    context = TraceContextTextMapPropagator().extract(carrier, context=Context())
    token = attach(context)
    try:
        with trace.get_tracer(__name__).start_as_current_span("worldcup.agent"):
            response = await hosted.invoke(principal, envelope.request)
    except Exception as error:
        logger.error("agent_invocation_failed error_type=%s", type(error).__name__)
        response = create_error_response("runtime_unavailable")
    finally:
        detach(token)
    return JSONResponse(response.model_dump(mode="json"))


if __name__ == "__main__":
    uvicorn.run(
        app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")), access_log=False
    )
