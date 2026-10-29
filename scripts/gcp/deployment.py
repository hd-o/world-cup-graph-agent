import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from scripts.gcp.configuration import Targets
from scripts.shared.configuration import ROOT
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4


LABELED_RESOURCE_TYPES = {
    "google_compute_disk",
    "google_compute_instance",
    "google_secret_manager_secret",
    "google_cloud_run_v2_service",
}

REGIONAL_RESOURCE_TYPES = {
    "google_compute_disk",
    "google_compute_instance",
    "google_compute_subnetwork",
    "google_compute_router",
    "google_compute_router_nat",
    "google_cloud_run_v2_service",
    "google_cloud_run_v2_service_iam_member",
}

IMMUTABLE_RESOURCE_FIELDS = {
    "id",
    "name",
    "instance_id",
    "disk_id",
    "network_id",
    "managed_zone_id",
    "self_link_unique",
    "unique_id",
    "uid",
    "creation_timestamp",
    "creation_time",
    "create_time",
    "project",
    "region",
    "location",
    "zone",
    "managed_zone",
    "service_account_id",
    "secret_id",
    "network",
    "subnetwork",
    "subnetworks",
    "role",
    "member",
    "condition",
    "role_id",
    "network_firewall_policy_id",
    "self_link_with_id",
    "firewall_policy",
    "attachment_target",
    "priority",
}


@dataclass(frozen=True)
class Plan:
    path: Path
    changes: list[dict] = field(repr=False)
    directory: Path = ROOT / "deploy/gcp"
    contents: bytes = field(default=b"", repr=False)

    def build_inventory(self) -> list[dict]:
        # Plans can contain source bundles and credentials; present only scope.
        return [
            {
                "address": change["address"],
                "actions": change["change"]["actions"],
                "before": filter_resource_settings(change["change"].get("before")),
                "after": filter_resource_settings(change["change"].get("after")),
            }
            for change in self.changes
            if change.get("mode") != "data"
        ]


def filter_resource_settings(values: dict | None) -> dict:
    names = {
        "name",
        "project",
        "region",
        "zone",
        "location",
        "labels",
        "display_name",
        "account_id",
        "role",
        "member",
        "condition",
        "permissions",
        "role_id",
        "network",
        "subnetwork",
        "subnetworks",
        "ip_cidr_range",
        "source_ranges",
        "destination_ranges",
        "target_tags",
        "allow",
        "machine_type",
        "size",
        "type",
        "managed_zone",
        "dns_name",
        "visibility",
        "rrdatas",
        "ttl",
        "firewall_policy",
        "attachment_target",
        "network_firewall_policy_id",
        "network_firewall_policy_enforcement_order",
        "priority",
        "direction",
        "action",
        "match",
        "disabled",
        "target_service_accounts",
        "target_secure_tags",
        "ports",
        "stack_type",
        "private_ip_google_access",
        "ingress",
        "deletion_protection",
        "secret_id",
        "service_account_id",
    }
    settings = {name: value for name, value in (values or {}).items() if name in names}
    if values and values.get("template"):
        template = values["template"][0]
        settings["template"] = {
            name: template[name]
            for name in (
                "service_account",
                "timeout",
                "max_instance_request_concurrency",
                "scaling",
                "vpc_access",
            )
            if name in template
        }
        settings["template"]["containers"] = [
            {
                "image": container.get("image"),
                "resources": container.get("resources"),
                "environment_names": sorted(
                    entry["name"] for entry in container.get("env", [])
                ),
            }
            for container in template.get("containers", [])
        ]
    return settings


def run_terraform(
    targets: Targets, arguments: list[str], directory: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["terraform", *arguments],
        cwd=directory or ROOT / "deploy/gcp",
        env=targets.build_environment(),
        capture_output=True,
        text=True,
        check=True,
        timeout=4200,
    )


def save_private_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
    ) as stream:
        stream.write(content)
    path.chmod(0o600)


def prepare_application_workspace(purpose: str) -> Path:
    from scripts.shared.invocations import get_generated_path

    folder = get_generated_path("private", purpose, str(uuid4()))
    folder.mkdir(parents=True, mode=0o700)
    for path in (ROOT / "deploy/gcp").glob("*.tf"):
        shutil.copyfile(path, folder / path.name)
    shutil.copyfile(
        ROOT / "deploy/gcp/.terraform.lock.hcl", folder / ".terraform.lock.hcl"
    )
    return folder


def load_project_identities(targets: Targets, session: Any) -> set[str]:
    response = session.get(
        f"https://cloudresourcemanager.googleapis.com/v1/projects/{targets.gcp_project}",
        timeout=30,
    )
    response.raise_for_status()
    project = response.json()
    if project["projectId"] != targets.gcp_project:
        raise PermissionError("Authenticated GCP project does not match target")
    return {targets.gcp_project, str(project["projectNumber"])}


def validate_resource_destination(
    identity: str, projects: set[str], region: str | None = None
) -> None:
    parsed = urlparse(identity)
    if parsed.scheme and (
        parsed.scheme != "https"
        or parsed.hostname
        not in {
            "www.googleapis.com",
            "compute.googleapis.com",
            "run.googleapis.com",
            "secretmanager.googleapis.com",
            "artifactregistry.googleapis.com",
        }
    ):
        raise PermissionError("Resource endpoint is not an authorized Google API")
    parts = parsed.path.strip("/").split("/")
    if "projects" not in parts or parts[parts.index("projects") + 1] not in projects:
        raise PermissionError("Resource project identity does not match")
    if region:
        for scope in ("regions", "locations", "zones"):
            if scope not in parts:
                continue
            location = parts[parts.index(scope) + 1]
            if location != region and not (
                scope == "zones" and location.startswith(region + "-")
            ):
                raise PermissionError("Resource region does not match")


def match_resource_identity(first: str, second: str, projects: set[str]) -> bool:
    identities = [
        re.search(r"(?:^|/)projects/([^/]+)/(.*)", identity)
        for identity in (first, second)
    ]
    if any(identity is None for identity in identities):
        return first == second
    first_identity, second_identity = identities
    return (
        first_identity is not None
        and second_identity is not None
        and first_identity[1] in projects
        and second_identity[1] in projects
        and first_identity[2] == second_identity[2]
    )


def derive_backend(targets: Targets) -> dict[str, str]:
    return {
        "bucket": f"{targets.gcp_project}-{targets.deployment}-tfstate",
        "prefix": "application",
    }


def initialize_backend(targets: Targets, directory: Path | None = None) -> None:
    from google.cloud import storage

    targets.create_google_session()
    backend = derive_backend(targets)
    bucket = storage.Client(project=targets.gcp_project).get_bucket(backend["bucket"])
    if bucket.labels != {
        "deployment": targets.deployment,
        "purpose": "terraform-state",
    }:
        raise PermissionError("GCS backend ownership labels do not match")
    if str(bucket.location).lower() != targets.gcp_region:
        raise PermissionError("GCS backend region does not match")
    run_terraform(
        targets,
        [
            "init",
            "-input=false",
            "-reconfigure",
            f"-backend-config=bucket={backend['bucket']}",
            f"-backend-config=prefix={backend['prefix']}",
        ],
        directory,
    )


def load_state(targets: Targets, directory: Path | None = None) -> dict:
    state = json.loads(run_terraform(targets, ["state", "pull"], directory).stdout)
    if not state.get("lineage"):
        raise PermissionError("Established remote application state is unavailable")
    return state


def list_state_resources(state: dict) -> list[dict]:
    return [
        {
            "address": f"{resource['type']}.{resource['name']}",
            "type": resource["type"],
            "instance_address": build_instance_address(resource, instance),
            "values": instance["attributes"],
        }
        for resource in state.get("resources", [])
        if resource.get("mode") == "managed"
        for instance in resource.get("instances", [])
    ]


def build_instance_address(resource: dict, instance: dict) -> str:
    address = f"{resource['type']}.{resource['name']}"
    if "index_key" in instance:
        address += f"[{json.dumps(instance['index_key'])}]"
    if instance.get("deposed"):
        address += f"@{instance['deposed']}"
    return address


def validate_resource_ownership(
    targets: Targets, kind: str, values: dict, projects: set[str]
) -> None:
    project = values.get("project")
    identities = [
        value
        for name in (
            "id",
            "name",
            "service_account_id",
            "secret_id",
            "firewall_policy",
            "attachment_target",
        )
        if isinstance(value := values.get(name), str)
        and re.search(r"(?:^|/)projects/", value)
    ]
    if project is not None and str(project) not in projects:
        raise PermissionError("Application resource contains another project")
    if not project and not identities:
        raise PermissionError("Application resource lacks an explicit project identity")
    region = None if kind == "google_apikeys_key" else targets.gcp_region
    for identity in identities:
        validate_resource_destination(identity, projects, region)
    if kind in REGIONAL_RESOURCE_TYPES and not (
        any(values.get(name) for name in ("region", "location", "zone"))
        or any(
            re.search(r"/(regions|locations|zones)/", identity)
            for identity in identities
        )
    ):
        raise PermissionError(
            "Application resource lacks an explicit region/zone identity"
        )
    for name in ("region", "location", "zone"):
        location = values.get(name)
        if not location:
            continue
        location = location.split("/")[-1]
        if kind == "google_apikeys_key" and name == "location" and location == "global":
            continue
        if location != targets.gcp_region and not (
            name == "zone" and location.startswith(targets.gcp_region + "-")
        ):
            raise PermissionError("Application resource contains another region/zone")
    labels = values.get("labels") or {}
    if (labels or kind in LABELED_RESOURCE_TYPES) and labels.get(
        "deployment"
    ) != targets.deployment:
        raise PermissionError(
            "Application resource contains unmatched ownership labels"
        )
    if kind == "google_compute_network_firewall_policy":
        if values.get("name") != f"{targets.deployment}-agent":
            raise PermissionError(
                "NGFW policy name does not match deployment ownership"
            )
    elif kind in (
        "google_compute_network_firewall_policy_association",
        "google_compute_network_firewall_policy_rule",
    ):
        if values.get("firewall_policy", "").split("/")[-1] != (
            f"{targets.deployment}-agent"
        ):
            raise PermissionError(
                "NGFW child policy does not match deployment ownership"
            )
        if kind == "google_compute_network_firewall_policy_association":
            network = (
                f"projects/{targets.gcp_project}/global/networks/{targets.deployment}"
            )
            if values.get("name") != f"{targets.deployment}-agent" or not (
                match_resource_identity(
                    values.get("attachment_target", ""), network, projects
                )
            ):
                raise PermissionError(
                    "NGFW association network does not match deployment"
                )


def validate_resource_identity(
    kind: str, stored_values: dict, refreshed_values: dict, projects: set[str]
) -> None:
    required_fields = {
        "google_compute_instance": ("instance_id", "creation_timestamp"),
        "google_compute_disk": ("disk_id", "creation_timestamp"),
        "google_compute_network": ("network_id",),
        "google_service_account": ("unique_id",),
        "google_cloud_run_v2_service": ("uid", "create_time"),
        "google_compute_network_firewall_policy": ("network_firewall_policy_id",),
    }
    if kind in required_fields and not any(
        stored_values.get(name) and refreshed_values.get(name)
        for name in required_fields[kind]
    ):
        raise PermissionError("Native immutable resource identity is unavailable")
    for name in IMMUTABLE_RESOURCE_FIELDS:
        stored_value = stored_values.get(name)
        refreshed_value = refreshed_values.get(name)
        if stored_value == refreshed_value:
            continue
        if (
            isinstance(stored_value, str)
            and isinstance(refreshed_value, str)
            and re.search(r"(?:^|/)projects/", stored_value)
            and match_resource_identity(stored_value, refreshed_value, projects)
        ):
            continue
        if (
            name == "project"
            and str(stored_value) in projects
            and str(refreshed_value) in projects
        ):
            continue
        raise PermissionError(
            "Native resource identity or ownership changed; review recovery"
        )


def iterate_ownership_history(
    targets: Targets, state: dict, session: Any
) -> Iterator[dict]:
    backend = derive_backend(targets)
    projects = load_project_identities(targets, session)
    endpoint = f"https://storage.googleapis.com/storage/v1/b/{backend['bucket']}"
    response = session.get(endpoint, timeout=30)
    response.raise_for_status()
    bucket = response.json()
    if (
        bucket.get("labels")
        != {"deployment": targets.deployment, "purpose": "terraform-state"}
        or str(bucket.get("projectNumber")) not in projects
        or bucket.get("location", "").lower() != targets.gcp_region
    ):
        raise PermissionError(
            "Backend native project, region or ownership does not match"
        )
    if not bucket.get("versioning", {}).get("enabled"):
        raise PermissionError(
            "Partial recovery requires protected versioned backend history"
        )
    object_name = f"{backend['prefix']}/default.tfstate"
    response = session.get(
        f"{endpoint}/o",
        params={"prefix": object_name, "versions": "true", "maxResults": 100},
        timeout=30,
    )
    response.raise_for_status()
    versions = sorted(
        response.json().get("items", []),
        key=lambda version: int(version["generation"]),
        reverse=True,
    )
    # Recovery uses immutable versions from the verified backend, never a local
    # report or an unrelated state lineage. Bounded history fails closed.
    for version in versions:
        if version["name"] != object_name:
            continue
        response = session.get(
            f"{endpoint}/o/{quote(object_name, safe='')}",
            params={"alt": "media", "generation": version["generation"]},
            timeout=30,
        )
        response.raise_for_status()
        history = response.json()
        if (
            history.get("lineage") != state["lineage"]
            or history.get("serial", -1) > state["serial"]
        ):
            continue
        network = next(
            (
                resource
                for resource in list_state_resources(history)
                if resource["address"] == "google_compute_network.application"
            ),
            None,
        )
        if network is None:
            continue
        validate_resource_ownership(
            targets, network["type"], network["values"], projects
        )
        if network["values"].get("name") != targets.deployment or not network[
            "values"
        ].get("network_id"):
            raise PermissionError(
                "Backend history lacks an immutable application network anchor"
            )
        yield history


def load_ownership_history(targets: Targets, state: dict, session: Any) -> dict:
    history = next(iterate_ownership_history(targets, state, session), None)
    if history is None:
        raise PermissionError(
            "Verified network lineage is outside available backend history"
        )
    return history


def load_deployment_outputs(targets: Targets) -> dict:
    folder = prepare_application_workspace("gcp-inspection")
    initialize_backend(targets, folder)
    state = load_state(targets, folder)
    validate_state_ownership(targets, state)
    return {
        name: output["value"] for name, output in state.get("outputs", {}).items()
    } | {
        "project": targets.gcp_project,
        "region": targets.gcp_region,
        "deployment": targets.deployment,
    }


def validate_state_ownership(targets: Targets, state: dict) -> None:
    addresses = {
        f"{kind}.{name}"
        for path in (ROOT / "deploy/gcp").glob("*.tf")
        for kind, name in re.findall(
            r'resource\s+"([^"]+)"\s+"([^"]+)"', path.read_text()
        )
    }
    if any(
        resource.get("module")
        or f"{resource['type']}.{resource['name']}" not in addresses
        for resource in state.get("resources", [])
        if resource.get("mode") == "managed"
    ):
        raise PermissionError(
            "Unexpected managed resource address in application state"
        )
    resources = list_state_resources(state)
    network = next(
        (
            resource
            for resource in resources
            if resource["address"] == "google_compute_network.application"
        ),
        None,
    )
    if not resources:
        return
    if not state.get("lineage"):
        raise PermissionError("Application state lacks an established lineage")
    session = targets.create_google_session()
    projects = load_project_identities(targets, session)
    history = None
    if network is None:
        history = load_ownership_history(targets, state, session)
    elif (
        network["values"].get("project") not in projects
        or network["values"].get("name") != targets.deployment
    ):
        raise PermissionError("Remote application state belongs to another deployment")
    history_resources = {
        resource["instance_address"]: resource
        for resource in list_state_resources(history or {})
    }
    for resource in resources:
        validate_resource_ownership(
            targets, resource["type"], resource["values"], projects
        )
        if history is not None:
            history_resource = history_resources.get(resource["instance_address"])
            if history_resource is None:
                raise PermissionError(
                    "Surviving resource lacks verified backend lineage"
                )
            validate_resource_identity(
                resource["type"],
                history_resource["values"],
                resource["values"],
                projects,
            )


def load_saved_plan(
    targets: Targets, path: Path, directory: Path | None = None
) -> Plan:
    path.chmod(0o600)
    payload = json.loads(
        run_terraform(targets, ["show", "-json", str(path)], directory).stdout
    )
    return Plan(
        path,
        payload.get("resource_changes", []),
        directory or ROOT / "deploy/gcp",
        path.read_bytes(),
    )


def load_existing_state(targets: Targets, directory: Path | None = None) -> dict | None:
    from google.cloud import storage

    backend = derive_backend(targets)
    bucket = storage.Client(project=targets.gcp_project).get_bucket(backend["bucket"])
    # Absence is checked in the authoritative backend, never inferred from a
    # subprocess failure that might instead mean unavailable credentials/state.
    if not bucket.blob(f"{backend['prefix']}/default.tfstate").exists():
        return None
    state = load_state(targets, directory)
    validate_state_ownership(targets, state)
    return state


def inspect_application_state(targets: Targets) -> dict | None:
    folder = prepare_application_workspace("gcp-inspection")
    initialize_backend(targets, folder)
    return load_existing_state(targets, folder)


def apply_reviewed_plan(targets: Targets, plan: Plan) -> None:
    # Comparing private bytes binds execution to the reviewed file without
    # exposing credentials or printing a hash of sensitive plan contents.
    if not plan.contents or plan.path.read_bytes() != plan.contents:
        raise PermissionError("Saved Terraform plan changed after review")
    run_terraform(targets, ["apply", "-input=false", str(plan.path)], plan.directory)


def list_google_items(
    session: Any,
    url: str,
    field: str,
    params: dict | None = None,
    token_name: str = "pageToken",
) -> list[dict]:
    records: list[dict] = []
    token = None
    while True:
        response = session.get(
            url,
            params=(params or {}) | ({token_name: token} if token else {}),
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        records.extend(payload.get(field, []))
        token = payload.get("nextPageToken")
        if not token:
            return records
