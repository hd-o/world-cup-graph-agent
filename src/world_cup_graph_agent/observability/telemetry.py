import json
import os
import re
from collections.abc import Sequence
from functools import cache
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Status
from typing import Any


_ATTRIBUTES = {
    "langsmith.span.kind",
    "langsmith.span.id",
    "langsmith.span.parent_id",
    "langsmith.trace.id",
    "langsmith.trace.name",
    "langsmith.span.dotted_order",
    "gen_ai.system",
    "gen_ai.operation.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.tool.name",
    "gen_ai.agent.name",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
    "gen_ai.usage.prompt_tokens",
    "gen_ai.usage.completion_tokens",
    "gen_ai.usage.details.reasoning_tokens",
    "http.request.method",
    "http.response.status_code",
    "error.type",
    "operation",
    "request_id",
    "thread_id",
    "runtime",
    "status",
    "aws.local.service",
    "aws.service.type",
}


class MetadataExporter(SpanExporter):
    def __init__(self, exporter: SpanExporter) -> None:
        self.exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        # Every enabled instrumentation path reaches this export boundary.
        # Rebuild via the public span API: events, links, raw error descriptions,
        # ingress envelopes and unlisted attributes never enter native storage.
        capture = os.getenv("TRACE_CONTENT_CAPTURE", "false").lower() == "true"
        sanitized = [
            ReadableSpan(
                name=span.name,
                context=span.context,
                parent=span.parent,
                resource=span.resource,
                kind=span.kind,
                attributes=filter_span_attributes(span.attributes or {}, capture),
                status=Status(span.status.status_code),
                start_time=span.start_time,
                end_time=span.end_time,
                instrumentation_scope=span.instrumentation_scope,
            )
            for span in spans
        ]
        return self.exporter.export(sanitized)

    def shutdown(self) -> None:
        self.exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.exporter.force_flush(timeout_millis)


@cache
def configure_cloud_telemetry(cloud: str, project: str = "") -> None:
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": os.getenv(
                        "OTEL_SERVICE_NAME", "worldcup-agent"
                    ),
                    "cloud.provider": "aws" if cloud == "aws" else "gcp",
                    "gcp.project_id": project,
                }
            )
        )
        trace.set_tracer_provider(provider)

    if cloud == "aws":
        from amazon.opentelemetry.distro.exporter.otlp.aws.traces import (
            otlp_aws_span_exporter,
        )
        from botocore.session import get_session

        region = os.environ["AWS_REGION"]
        exporter = otlp_aws_span_exporter.OTLPAwsSpanExporter(
            session=get_session(),
            aws_region=region,
            endpoint=f"https://xray.{region}.amazonaws.com/v1/traces",
            headers={
                "x-aws-log-group": os.environ["APPLICATION_LOG_GROUP"],
                "x-aws-log-stream": "spans",
            },
        )
    else:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
            quota_project_id=project,
        )
        session = AuthorizedSession(credentials)
        session.trust_env = False
        exporter = OTLPSpanExporter(
            endpoint="https://telemetry.googleapis.com/v1/traces",
            session=session,
            timeout=10,
            headers={"x-goog-user-project": project},
        )

    provider.add_span_processor(BatchSpanProcessor(MetadataExporter(exporter)))
    os.environ.update(
        {
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_OTEL_ENABLED": "true",
            "LANGSMITH_OTEL_ONLY": "true",
        }
    )


def create_tracing_client() -> Any:
    from langsmith import Client

    # Mask before the LangSmith bridge serializes content, then filter again at
    # the cloud export boundary to cover independent HTTP instrumentation too.
    capture = os.getenv("TRACE_CONTENT_CAPTURE", "false").lower() == "true"
    return Client(
        hide_inputs=sanitize_trace_content if capture else lambda _: {},
        hide_outputs=sanitize_trace_content if capture else extract_usage_metadata,
    )


def extract_usage_metadata(content: Any) -> dict:
    if isinstance(content, dict):
        usage = content.get("usage_metadata")
        if isinstance(usage, dict) and all(
            type(usage.get(name)) is int for name in ("input_tokens", "output_tokens")
        ):
            return {
                "usage_metadata": {
                    name: usage[name] for name in ("input_tokens", "output_tokens")
                }
            }

        children = content.values()
    elif isinstance(content, (list, tuple)):
        children = content
    else:
        return {}

    for child in children:
        metadata = extract_usage_metadata(child)
        if metadata:
            return metadata

    return {}


def filter_span_attributes(attributes: Any, capture: bool) -> dict:
    metadata = {
        name: value for name, value in attributes.items() if name in _ATTRIBUTES
    }
    if capture:
        for name in ("gen_ai.prompt", "gen_ai.completion"):
            content = attributes.get(name)
            if isinstance(content, (str, bytes)):
                try:
                    content = json.loads(content)
                except ValueError, TypeError:
                    continue

                metadata[name] = json.dumps(sanitize_trace_content(content))[:4000]

    return metadata


def sanitize_trace_content(content: Any) -> Any:
    if isinstance(content, dict):
        return {
            name: sanitize_trace_content(value)
            for name, value in content.items()
            if not re.search(
                r"credential|password|token|authorization|secret|api_key",
                name,
                re.IGNORECASE,
            )
        }
    if isinstance(content, (list, tuple)):
        return [sanitize_trace_content(value) for value in content]
    if isinstance(content, str):
        return re.sub(
            r"sk-[A-Za-z0-9_-]+|Bearer\s+\S+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
            "[REDACTED]",
            content,
        )[:4000]
    return content
