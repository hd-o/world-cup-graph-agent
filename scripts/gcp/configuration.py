import os
from dataclasses import dataclass, field
from pydantic import SecretStr
from scripts.gcp.settings import get_gcp_inputs, load_gcp_settings
from scripts.shared.configuration import ROOT
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from google.auth.transport.requests import AuthorizedSession


@dataclass(frozen=True)
class Targets:
    deployment: str
    gcp_project: str
    gcp_region: str
    inputs: dict[str, SecretStr] = field(repr=False)

    def create_google_session(self) -> AuthorizedSession:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
            quota_project_id=self.gcp_project,
        )

        session = AuthorizedSession(credentials)
        session.trust_env = False
        response = session.get(
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{self.gcp_project}",
            timeout=30,
        )

        response.raise_for_status()
        if response.json()["projectId"] != self.gcp_project:
            raise PermissionError("Authenticated GCP project does not match target")
        return session

    def build_environment(self) -> dict[str, str]:
        # Protected operator inputs must not cross the subprocess boundary.
        environment = {
            name: value for name, value in os.environ.items() if name not in self.inputs
        }
        return environment | {
            "GOOGLE_CLOUD_PROJECT": self.gcp_project,
            "CLOUDSDK_CORE_PROJECT": self.gcp_project,
            "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT),
        }


def load_targets(overrides: dict[str, str] | None = None) -> Targets:
    settings = load_gcp_settings(overrides).require_routing()
    return Targets(
        deployment=settings.deployment_name,
        gcp_project=settings.google_cloud_project,
        gcp_region=settings.gcp_region,
        inputs=get_gcp_inputs(settings),
    )
