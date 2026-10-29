import json
import os
import subprocess
from dataclasses import dataclass, field
from pydantic import SecretStr
from scripts.aws.settings import get_aws_inputs, load_aws_settings
from scripts.shared.configuration import ROOT
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import boto3


AWS_TELEMETRY = {
    "PYTHONPATH": "/var/task",
    "DISABLE_ADOT_OBSERVABILITY": "true",
    "OTEL_PYTHON_DISTRO": "aws_distro",
    "OTEL_PYTHON_CONFIGURATOR": "aws_configurator",
    "OTEL_RESOURCE_ATTRIBUTES": "aws.service.type=gen_ai_agent",
    "AGENT_OBSERVABILITY_ENABLED": "false",
    "AWS_GENAI_INSTRUMENTATION": "disabled",
    "OTEL_TRACES_EXPORTER": "none",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "none",
    "OTEL_AWS_APPLICATION_SIGNALS_ENABLED": "false",
    "OTEL_AWS_SERVICE_EVENTS_ENABLED": "false",
    "OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED": "false",
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false",
    "OTEL_PROPAGATORS": "tracecontext",
    "LANGSMITH_TRACING": "true",
    "LANGSMITH_OTEL_ENABLED": "true",
    "LANGSMITH_OTEL_ONLY": "true",
}


@dataclass(frozen=True)
class Targets:
    deployment: str
    aws_profile: str
    aws_account: str
    aws_region: str
    inputs: dict[str, SecretStr] = field(repr=False)

    def create_aws_session(self) -> boto3.Session:
        import boto3
        from botocore.config import Config
        from botocore.session import Session

        response = subprocess.run(
            [
                "aws",
                "configure",
                "export-credentials",
                "--profile",
                self.aws_profile,
                "--format",
                "process",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )

        credentials = json.loads(response.stdout)
        authentication = Session()
        # An uncertain mutation is an operator recovery decision, not permission
        # for the SDK to resubmit it automatically after a lost response.
        authentication.set_default_client_config(
            Config(
                connect_timeout=10,
                read_timeout=60,
                retries={"total_max_attempts": 1, "mode": "standard"},
            )
        )
        session = boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials.get("SessionToken"),
            region_name=self.aws_region,
            botocore_session=authentication,
        )

        if session.client("sts").get_caller_identity()["Account"] != self.aws_account:
            raise PermissionError("Authenticated AWS account does not match target")
        return session

    def build_environment(self) -> dict[str, str]:
        # Protected inputs never enter child process arguments or environment.
        environment = {
            name: value
            for name, value in os.environ.items()
            if name not in self.inputs
            and name != "AWS_ACCESS_KEY_ID"
            and not any(
                token in name.upper()
                for token in ("PASSWORD", "SECRET", "TOKEN", "API_KEY")
            )
        }
        return environment | {
            "DEPLOYMENT_NAME": self.deployment,
            "AWS_ACCOUNT_ID": self.aws_account,
            "AWS_PROFILE": self.aws_profile,
            "AWS_REGION": self.aws_region,
            "AWS_DEFAULT_REGION": self.aws_region,
            "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT),
        }

    def build_aws_environment(self) -> dict[str, str]:
        credentials = self.create_aws_session().get_credentials()
        if credentials is None:
            raise PermissionError("AWS deployment credentials are unavailable")
        credentials = credentials.get_frozen_credentials()
        if not credentials.access_key or not credentials.secret_key:
            raise PermissionError("AWS deployment credentials are incomplete")
        return self.build_environment() | {
            "AWS_ACCESS_KEY_ID": credentials.access_key,
            "AWS_SECRET_ACCESS_KEY": credentials.secret_key,
            "AWS_SESSION_TOKEN": credentials.token or "",
        }


def load_targets(overrides: dict[str, str] | None = None) -> Targets:
    settings = load_aws_settings(overrides).require_routing()
    return Targets(
        deployment=settings.deployment_name,
        aws_profile=settings.aws_profile,
        aws_account=settings.aws_account_id,
        aws_region=settings.aws_region,
        inputs=get_aws_inputs(settings),
    )
