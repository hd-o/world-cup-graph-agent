import time
from scripts.gcp.configuration import Targets
from scripts.gcp.deployment import derive_backend, list_google_items
from scripts.shared.configuration import save_outputs
from typing import Any


SERVICES = (
    "compute.googleapis.com",
    "dns.googleapis.com",
    "secretmanager.googleapis.com",
    "identitytoolkit.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "telemetry.googleapis.com",
    "cloudtrace.googleapis.com",
    "iam.googleapis.com",
    "iap.googleapis.com",
    "oslogin.googleapis.com",
    "storage.googleapis.com",
    "apikeys.googleapis.com",
)


def wait_for_operation(
    session: Any, endpoint: str, operation: dict, seconds: int = 300
) -> dict:
    deadline = time.monotonic() + seconds
    while not operation.get("done"):
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Cloud operation is pending; inspect status before resubmitting"
            )
        time.sleep(2)
        response = session.get(f"{endpoint}/{operation['name']}", timeout=30)
        response.raise_for_status()
        operation = response.json()
    if operation.get("error"):
        raise RuntimeError(
            "Cloud operation failed; inspect its protected operator details"
        )
    return operation


def verify_bootstrap(targets: Targets) -> None:
    inspection = inspect_bootstrap(targets)
    if any(
        inspection[name]
        for name in (
            "enable_services",
            "initialize_identity_platform",
            "enable_tenants",
            "create_backend",
        )
    ):
        raise PermissionError("Retained project bootstrap is not verified")


def inspect_bootstrap(targets: Targets) -> dict:
    from google.cloud import storage

    session = targets.create_google_session()
    project = f"projects/{targets.gcp_project}"
    services = list_google_items(
        session,
        f"https://serviceusage.googleapis.com/v1/{project}/services",
        "services",
        {"filter": "state:ENABLED"},
    )
    enabled = {service["config"]["name"] for service in services}
    response = session.get(
        f"https://identitytoolkit.googleapis.com/v2/{project}/config", timeout=30
    )
    identity = {}
    if response.status_code not in (403, 404):
        response.raise_for_status()
        identity = response.json()
    elif "identitytoolkit.googleapis.com" in enabled and response.status_code == 403:
        response.raise_for_status()
    backend = derive_backend(targets)
    bucket = storage.Client(project=targets.gcp_project).lookup_bucket(
        backend["bucket"]
    )
    if bucket is not None:
        if (
            bucket.labels
            != {"deployment": targets.deployment, "purpose": "terraform-state"}
            or str(bucket.location).lower() != targets.gcp_region
        ):
            raise PermissionError(
                "Existing backend bucket ownership or region does not match"
            )
        if (
            not bucket.versioning_enabled
            or not bucket.iam_configuration.uniform_bucket_level_access_enabled
            or bucket.iam_configuration.public_access_prevention != "enforced"
        ):
            raise PermissionError(
                "Backend protection differs; inspect retained storage before deploying"
            )
    return {
        "project": targets.gcp_project,
        "region": targets.gcp_region,
        "deployment": targets.deployment,
        "enable_services": sorted(set(SERVICES) - enabled),
        "initialize_identity_platform": not identity,
        "enable_tenants": not identity.get("multiTenant", {}).get("allowTenants"),
        "create_backend": bucket is None,
        "backend": backend,
    }


def bootstrap_google(targets: Targets, inspection: dict | None = None) -> None:
    from google.cloud import storage

    inspection = inspection or inspect_bootstrap(targets)
    # Re-inspection prevents approval of one scope from granting a broader write.
    if inspect_bootstrap(targets) != inspection:
        raise PermissionError("Bootstrap inventory changed; review it again")
    session = targets.create_google_session()
    project = f"projects/{targets.gcp_project}"
    if inspection["enable_services"]:
        endpoint = "https://serviceusage.googleapis.com/v1"
        response = session.post(
            f"{endpoint}/{project}/services:batchEnable",
            json={"serviceIds": inspection["enable_services"]},
            timeout=30,
        )
        response.raise_for_status()
        wait_for_operation(session, endpoint, response.json())
    endpoint = f"https://identitytoolkit.googleapis.com/v2/{project}"
    if inspection["initialize_identity_platform"]:
        response = session.post(
            f"{endpoint}/identityPlatform:initializeAuth", json={}, timeout=30
        )
        response.raise_for_status()
    if inspection["enable_tenants"]:
        response = session.patch(
            f"{endpoint}/config",
            params={"updateMask": "multiTenant.allowTenants"},
            json={"multiTenant": {"allowTenants": True}},
            timeout=30,
        )
        response.raise_for_status()
    if inspection["create_backend"]:
        client = storage.Client(project=targets.gcp_project)
        bucket = client.bucket(inspection["backend"]["bucket"])
        bucket.labels = {"deployment": targets.deployment, "purpose": "terraform-state"}
        bucket.iam_configuration.uniform_bucket_level_access_enabled = True
        bucket.iam_configuration.public_access_prevention = "enforced"
        bucket.versioning_enabled = True
        client.create_bucket(bucket, location=targets.gcp_region)
    verify_bootstrap(targets)
    save_outputs(
        "gcp-bootstrap", inspection | {"status": "retained bootstrap verified"}
    )
