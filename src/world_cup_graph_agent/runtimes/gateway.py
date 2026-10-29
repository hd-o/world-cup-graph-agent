import asyncio
import logging
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.auth.compute_engine import IDTokenCredentials
from google.auth.transport.requests import AuthorizedSession
from google.auth.transport.requests import Request as AuthRequest
from opentelemetry import trace
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from pydantic import ValidationError
from requests import Session
from world_cup_graph_agent.observability.telemetry import configure_cloud_telemetry
from world_cup_graph_agent.runtimes.contracts import (
    HostedInvocation,
    create_error_response,
    request_adapter,
)
from world_cup_graph_agent.runtimes.identity import verify_google_token


project = os.environ["GOOGLE_CLOUD_PROJECT"]
tenant = os.environ["IDENTITY_TENANT"]
agent_url = os.environ["AGENT_URL"].rstrip("/")
configure_cloud_telemetry("gcp", project)
credential_session = Session()
credential_session.trust_env = False
credential_request = AuthRequest(session=credential_session)
credentials = IDTokenCredentials(
    credential_request,
    target_audience=agent_url,
    use_metadata_identity_endpoint=True,
)
# Refresh credentials before sending, but do not resend an invocation after
# any HTTP response: transport/auth failures cannot establish replay safety.
session = AuthorizedSession(
    credentials, auth_request=credential_request, max_refresh_attempts=0
)
session.trust_env = False
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
logger = logging.getLogger(__name__)
app.add_middleware(
    OpenTelemetryMiddleware,
    excluded_urls="/health",
    default_span_details=lambda scope: ("worldcup.gateway", {}),
)


@app.get("/health")
async def read_health() -> dict:
    return {"status": "ready"}


@app.post("/invoke")
async def invoke_agent(request: Request) -> JSONResponse:
    try:
        principal = await asyncio.to_thread(
            verify_google_token,
            request.headers.get("authorization", ""),
            project,
            tenant,
        )
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 100000:
                raise ValueError("invalid_request")
            body.extend(chunk)

        operation = request_adapter.validate_json(bytes(body))
    except PermissionError:
        return JSONResponse(
            create_error_response("unauthenticated").model_dump(mode="json"),
            status_code=401,
        )
    except ValidationError, ValueError:
        return JSONResponse(
            create_error_response("invalid_request").model_dump(mode="json"),
            status_code=400,
        )
    except Exception as error:
        logger.error("hosted_identity_failed error_type=%s", type(error).__name__)
        permission_response = getattr(error, "http_response", None)
        if permission_response is not None:
            try:
                details = permission_response.json().get("error", {}).get("details", [])
            except ValueError:
                details = []
            # Only documented machine-readable denial reasons enter logs.
            # The response body can contain credentials and is never logged.
            for detail in details:
                reason = detail.get("reason")
                if reason in {
                    "IAM_PERMISSION_DENIED",
                    "SERVICE_DISABLED",
                    "USER_PROJECT_DENIED",
                    "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
                    "BILLING_DISABLED",
                    "CONSUMER_INVALID",
                }:
                    logger.error("hosted_identity_denied reason=%s", reason)
        return JSONResponse(
            create_error_response("runtime_unavailable").model_dump(mode="json"),
            status_code=503,
        )

    context = trace.get_current_span().get_span_context()
    # Version 00 carries only the sampled bit; SDK-specific flags stay local.
    carrier = (
        f"00-{context.trace_id:032x}-{context.span_id:016x}-0{int(context.trace_flags.sampled)}"
        if context.is_valid
        else None
    )
    invocation = HostedInvocation(
        principal=principal, request=operation, traceparent=carrier
    )
    status_code = 0
    try:
        # Only this endpoint is selectable. IAM uses the gateway's workload identity;
        # the original application JWT is not propagated or serialized.
        response = await asyncio.to_thread(
            session.post,
            f"{agent_url}/invoke",
            data=invocation.model_dump_json().encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=550,
            allow_redirects=False,
        )
        status_code = response.status_code
        response.raise_for_status()
        return JSONResponse(response.json())
    except Exception as error:
        # A downstream timeout does not prove that execution failed. No retries:
        # callers inspect their conversation before any further execution.
        # Fixed metadata lets operators distinguish IAM/transport failures
        # without copying error bodies, credentials or conversation payloads.
        logger.error(
            "hosted_downstream_failed status_code=%d error_type=%s",
            status_code,
            type(error).__name__,
        )
        return JSONResponse(
            create_error_response("runtime_unavailable").model_dump(mode="json"),
            status_code=503,
        )
