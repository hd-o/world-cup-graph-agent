import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from scripts.gcp.bootstrap import verify_bootstrap, wait_for_operation
from scripts.gcp.configuration import Targets
from scripts.gcp.deployment import (
    Plan,
    apply_reviewed_plan,
    initialize_backend,
    iterate_ownership_history,
    list_google_items,
    list_state_resources,
    load_ownership_history,
    load_project_identities,
    load_saved_plan,
    load_state,
    match_resource_identity,
    run_terraform,
    save_private_file,
    validate_resource_destination,
    validate_resource_identity,
    validate_resource_ownership,
    validate_state_ownership,
)
from scripts.shared.configuration import ROOT, save_outputs
from typing import Any
from uuid import uuid4


DATA_LOSS_CONFIRMATION = "DELETE_DATABASE_USERS_AND_KEYS"
RETAINED_BOUNDARIES = [
    "GCS backend/bootstrap buckets and object versions",
    "project APIs and Identity Platform initialization",
    "project IAM grants outside application state",
    "unrelated project users/resources",
    "Artifact Registry repository and other packages",
    "independent cloud trace/audit retention",
    "external OpenRouter accounts/keys",
]


@dataclass(frozen=True)
class Teardown:
    plan: Plan
    inventory: dict


def prepare_destroy_workspace(targets: Targets) -> Path:
    from scripts.shared.invocations import get_generated_path

    folder = get_generated_path("private", "gcp-destroy", str(uuid4()))
    # Destroy needs only the pinned provider, backend and authoritative state.
    # Omitting application configuration prevents evaluating serving artifacts.
    # https://developer.hashicorp.com/terraform/cli/commands/plan#planning-modes
    configuration = {
        "terraform": {
            "required_version": "= 1.16.5",
            "backend": {"gcs": {}},
            "required_providers": {
                "google": {"source": "hashicorp/google", "version": "= 8.6.0"}
            },
        },
        "provider": {
            "google": {
                "project": targets.gcp_project,
                "region": targets.gcp_region,
                "billing_project": targets.gcp_project,
                "user_project_override": True,
            }
        },
    }
    save_private_file(folder / "terraform.tf.json", json.dumps(configuration))
    shutil.copyfile(
        ROOT / "deploy/gcp/.terraform.lock.hcl", folder / ".terraform.lock.hcl"
    )
    return folder


def validate_destroy_plan(targets: Targets, plan: Plan, state: dict) -> None:
    validate_state_ownership(targets, state)
    resources = {
        resource["instance_address"]: resource
        for resource in list_state_resources(state)
    }
    if len(resources) != len(list_state_resources(state)):
        raise PermissionError("Application state contains ambiguous resource instances")
    projects = load_project_identities(targets, targets.create_google_session())
    for change in plan.changes:
        if change.get("mode") == "data":
            continue
        address = change["address"]
        if change.get("deposed"):
            address += f"@{change['deposed']}"
        if address not in resources or change["change"]["actions"] not in (
            ["delete"],
            ["no-op"],
        ):
            raise PermissionError(
                "Destroy plan scope differs from the verified application state"
            )
        values = change["change"].get("before")
        if values is None or change["type"] != resources[address]["type"]:
            raise PermissionError(
                "Destroy plan lacks its verified native resource identity"
            )
        validate_resource_ownership(targets, change["type"], values, projects)
        validate_resource_identity(
            change["type"], resources[address]["values"], values, projects
        )


def verify_destroy_scope(targets: Targets, plan: Plan, state: dict) -> None:
    # Provider refresh reads native ownership/immutable IDs again without
    # replacing the approved plan or granting a changed deletion scope.
    path = plan.directory / f"verification-{uuid4()}.tfplan"
    run_terraform(
        targets, ["plan", "-destroy", "-input=false", f"-out={path}"], plan.directory
    )
    verification = load_saved_plan(targets, path, plan.directory)
    validate_destroy_plan(targets, verification, state)
    changes = {
        (change["address"], change.get("deposed")): change
        for change in verification.changes
        if change.get("mode") != "data"
    }
    reviewed_changes = [
        change for change in plan.changes if change.get("mode") != "data"
    ]
    if set(changes) != {
        (change["address"], change.get("deposed")) for change in reviewed_changes
    }:
        raise PermissionError(
            "Native application inventory changed after destroy review"
        )
    projects = load_project_identities(targets, targets.create_google_session())
    for change in reviewed_changes:
        current_change = changes[(change["address"], change.get("deposed"))]
        if current_change["change"]["actions"] != change["change"]["actions"]:
            raise PermissionError(
                "Native application actions changed after destroy review"
            )
        validate_resource_identity(
            change["type"],
            change["change"]["before"],
            current_change["change"]["before"],
            projects,
        )
    _, networking = list_application_orphans(
        targets.create_google_session(), targets, state
    )
    if networking:
        raise PermissionError(
            "Networking ownership changed; review recovery before teardown"
        )


def plan_google_teardown(targets: Targets) -> tuple[Plan, dict]:
    folder = prepare_destroy_workspace(targets)
    initialize_backend(targets, folder)
    state = load_state(targets, folder)
    validate_state_ownership(targets, state)
    verify_bootstrap(targets)
    save_private_file(folder / "application.tfstate.snapshot", json.dumps(state))
    path = folder / "destroy.tfplan"
    run_terraform(targets, ["plan", "-destroy", "-input=false", f"-out={path}"], folder)
    plan = load_saved_plan(targets, path, folder)
    validate_destroy_plan(targets, plan, state)
    return plan, state


def list_model_secrets(session: Any, targets: Targets) -> list[dict]:
    parent = f"projects/{targets.gcp_project}"
    projects = load_project_identities(targets, session)
    secrets = list_google_items(
        session, f"https://secretmanager.googleapis.com/v1/{parent}/secrets", "secrets"
    )
    records = []
    for secret in secrets:
        name = secret["name"]
        if secret.get("labels", {}).get("deployment") != targets.deployment:
            continue
        validate_resource_destination(name, projects)
        if "/secrets/" not in name or not name.split("/")[-1].startswith(
            targets.deployment + "-users-"
        ):
            continue
        response = session.get(
            f"https://secretmanager.googleapis.com/v1/{name}", timeout=30
        )
        if response.status_code == 404:
            continue
        response.raise_for_status()
        metadata = response.json()
        if metadata.get("labels", {}).get("deployment") != targets.deployment:
            raise PermissionError("Dynamic secret ownership changed during discovery")
        records.append(
            {
                "type": "model_secret",
                "identity": name,
                "url": f"https://secretmanager.googleapis.com/v1/{name}",
                "labels": metadata["labels"],
                "create_time": metadata.get("createTime"),
            }
        )
    return records


def list_image_versions(session: Any, targets: Targets) -> list[dict]:
    projects = load_project_identities(targets, session)
    parent = f"projects/{targets.gcp_project}/locations/{targets.gcp_region}"
    repository = f"{parent}/repositories/{targets.deployment}"
    endpoint = "https://artifactregistry.googleapis.com/v1"
    response = session.get(f"{endpoint}/{repository}", timeout=30)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    if (
        response.json().get("labels", {}).get("deployment") != targets.deployment
        or response.json().get("format") != "DOCKER"
    ):
        raise PermissionError("Artifact repository ownership/type does not match")
    records = []
    for service in ("agent", "gateway"):
        package = f"{repository}/packages/{service}"
        response = session.get(f"{endpoint}/{package}", timeout=30)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        versions = list_google_items(
            session, f"{endpoint}/{package}/versions", "versions"
        )
        for version in versions:
            name = version["name"]
            validate_resource_destination(name, projects, targets.gcp_region)
            if not name.startswith(f"{package}/versions/"):
                raise PermissionError("Artifact version package does not match")
            records.append(
                {
                    "type": "image_version",
                    "identity": name,
                    "url": f"{endpoint}/{name}",
                    "repository": repository,
                    "create_time": version.get("createTime"),
                    "media_type": version.get("metadata", {}).get("mediaType"),
                    "subject_digest": version.get("metadata", {}).get("subjectDigest"),
                }
            )
    return records


def load_firewall_policy_anchors(
    session: Any,
    targets: Targets,
    state: dict,
    network_id: str | None,
    history: dict | None,
    policies: list[dict],
) -> set[str]:
    anchors = {
        str(resource["values"]["network_firewall_policy_id"])
        for resource in list_state_resources(history or state)
        if resource["type"] == "google_compute_network_firewall_policy"
        and resource["values"].get("network_firewall_policy_id")
    }
    candidates = {
        str(policy["id"])
        for policy in policies
        if policy.get("name") == f"{targets.deployment}-agent" and policy.get("id")
    } - anchors
    if not candidates:
        return anchors
    projects = load_project_identities(targets, session)
    # A detached policy can outlive its state entry while the network remains.
    # Search only protected versions in this exact immutable network lineage;
    # a policy name alone never authorizes teardown ownership.
    for version in iterate_ownership_history(targets, state, session):
        resources = list_state_resources(version)
        network = next(
            resource["values"]
            for resource in resources
            if resource["address"] == "google_compute_network.application"
        )
        if str(network["network_id"]) != str(network_id):
            continue
        for resource in resources:
            if resource["address"] != "google_compute_network_firewall_policy.agent":
                continue
            validate_resource_ownership(
                targets, resource["type"], resource["values"], projects
            )
            if resource["values"].get("network_firewall_policy_id"):
                anchors.add(str(resource["values"]["network_firewall_policy_id"]))
        if candidates <= anchors:
            break
    return anchors


def list_firewall_policy_dependencies(
    session: Any,
    targets: Targets,
    state: dict,
    network_url: str,
    network_id: str | None,
    history: dict | None,
) -> list[dict]:
    projects = load_project_identities(targets, session)
    resources = list_state_resources(state)
    pending = []
    policies = list_google_items(
        session,
        f"https://compute.googleapis.com/compute/v1/projects/{targets.gcp_project}/global/firewallPolicies",
        "items",
    )
    anchors = load_firewall_policy_anchors(
        session, targets, state, network_id, history, policies
    )
    for policy in policies:
        associations = policy.get("associations", [])
        targets_by_association = {
            association["name"]: association.get("attachmentTarget", "")
            for association in associations
        }
        # A matching name is not ownership. Only the verified native network
        # reference or a policy's immutable ID in its network lineage qualifies.
        if (
            not any(
                match_resource_identity(target, network_url, projects)
                for target in targets_by_association.values()
            )
            and str(policy.get("id")) not in anchors
        ):
            continue
        if not network_id or not policy.get("id"):
            raise PermissionError("NGFW discovery lacks immutable ownership identity")
        identity = policy["selfLink"]
        validate_resource_destination(identity, projects)
        policy_resources = [
            resource
            for resource in resources
            if resource["type"].startswith("google_compute_network_firewall_policy")
            and (
                match_resource_identity(
                    resource["values"].get("id", ""), identity, projects
                )
                or resource["values"].get("firewall_policy", "").split("/")[-1]
                == policy["name"]
            )
        ]
        policy_owners = [
            resource
            for resource in policy_resources
            if resource["type"] == "google_compute_network_firewall_policy"
            and str(resource["values"].get("network_firewall_policy_id"))
            == str(policy["id"])
        ]
        association_owners = {
            resource["values"]["name"]: resource["values"]["attachment_target"]
            for resource in policy_resources
            if resource["type"] == "google_compute_network_firewall_policy_association"
        }
        priorities = {
            resource["values"]["priority"]
            for resource in policy_resources
            if resource["type"] == "google_compute_network_firewall_policy_rule"
        }
        # Default policy rules are server-owned. All other rules and every
        # association must be state-owned before deleting the enclosing policy.
        # https://cloud.google.com/firewall/docs/network-firewall-policies
        orphan_priorities = [
            rule["priority"]
            for rule in policy.get("rules", [])
            if rule["priority"] < 2147483644 and rule["priority"] not in priorities
        ]
        if (
            policy_owners
            and not orphan_priorities
            and all(
                name in association_owners
                and match_resource_identity(association_owners[name], target, projects)
                and match_resource_identity(target, network_url, projects)
                for name, target in targets_by_association.items()
            )
        ):
            continue
        pending.append(
            {
                "type": "firewall_policy",
                "identity": identity,
                "native_id": str(policy["id"]),
                "network": network_url,
                "network_id": str(network_id),
                "associations": targets_by_association,
                "orphan_priorities": orphan_priorities,
                "reason": "Recover/import NGFW state; no automatic detach/deletion",
            }
        )
    return pending


def list_application_orphans(
    session: Any, targets: Targets, state: dict
) -> tuple[list[dict], list[dict]]:
    parent = f"projects/{targets.gcp_project}"
    projects = load_project_identities(targets, session)
    state_ids = {
        resource["values"].get("id") for resource in list_state_resources(state)
    }
    orphans = []
    pending = []
    for collection in ("instances", "disks"):
        # Aggregated Compute lists use a map instead of a conventional page list.
        token = None
        while True:
            response = session.get(
                f"https://compute.googleapis.com/compute/v1/{parent}/aggregated/{collection}",
                params={"pageToken": token} if token else {},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            for scope, group in payload.get("items", {}).items():
                if not scope.startswith(f"zones/{targets.gcp_region}-"):
                    continue
                for resource in group.get(collection, []):
                    if (
                        resource.get("labels", {}).get("deployment")
                        != targets.deployment
                    ):
                        continue
                    validate_resource_destination(
                        resource["selfLink"], projects, targets.gcp_region
                    )
                    if any(
                        match_resource_identity(
                            resource["selfLink"], identity, projects
                        )
                        for identity in state_ids
                        if identity
                    ):
                        continue
                    orphans.append(
                        {
                            "type": collection[:-1],
                            "identity": resource["selfLink"],
                            "url": resource["selfLink"],
                            "labels": resource["labels"],
                            "native_id": resource["id"],
                            "zone": scope.split("/")[-1],
                        }
                    )
            token = payload.get("nextPageToken")
            if not token:
                break
    for endpoint, collection in (("https://run.googleapis.com/v2", "services"),):
        location = f"{parent}/locations/{targets.gcp_region}"
        for resource in list_google_items(
            session, f"{endpoint}/{location}/{collection}", collection
        ):
            if resource.get("labels", {}).get("deployment") != targets.deployment:
                continue
            name = resource["name"]
            validate_resource_destination(name, projects, targets.gcp_region)
            if any(
                match_resource_identity(name, identity, projects)
                for identity in state_ids
                if identity
            ):
                continue
            if f"/locations/{targets.gcp_region}/{collection}/" not in name:
                raise PermissionError("Orphan service destination does not match")
            orphans.append(
                {
                    "type": collection,
                    "identity": name,
                    "url": f"{endpoint}/{name}",
                    "labels": resource["labels"],
                    "create_time": resource.get("createTime"),
                }
            )
    # Network names have no labels. Only a state-lineage anchor and verified
    # native network references can establish ownership for external cleanup.
    network = next(
        (
            resource["values"]
            for resource in list_state_resources(state)
            if resource["address"] == "google_compute_network.application"
        ),
        None,
    )
    history: dict | None = None
    if network is None:
        history = load_ownership_history(targets, state, session)
        network = next(
            resource["values"]
            for resource in list_state_resources(history)
            if resource["address"] == "google_compute_network.application"
        )
    network_url = (
        network.get("self_link")
        or f"https://www.googleapis.com/compute/v1/{parent}/global/networks/{network['name']}"
    )
    response = session.get(network_url, timeout=30)
    network_id = network.get("network_id")
    if response.status_code != 404:
        response.raise_for_status()
        if not network_id or str(response.json().get("id")) != str(network_id):
            raise PermissionError(
                "Application network was replaced after its state ownership anchor"
            )
        if history is not None:
            orphans.append(
                {
                    "type": "networks",
                    "identity": network_url,
                    "url": network_url,
                    "native_id": str(response.json()["id"]),
                    "state_lineage": state["lineage"],
                    # State-owned subnets must finish deletion before their
                    # history-verified parent network can be removed.
                    "phase": "after_application",
                }
            )
    policy_dependencies = list_firewall_policy_dependencies(
        session, targets, state, network_url, network_id, history
    )
    pending.extend(policy_dependencies)
    for scope, collection in (
        ("global", "firewalls"),
        (f"regions/{targets.gcp_region}", "routers"),
        (f"regions/{targets.gcp_region}", "subnetworks"),
    ):
        records = list_google_items(
            session,
            f"https://compute.googleapis.com/compute/v1/{parent}/{scope}/{collection}",
            "items",
        )
        for resource in records:
            links = [resource.get("network", "")]
            if not links or any(
                not match_resource_identity(link, network_url, projects)
                for link in links
            ):
                continue
            validate_resource_destination(
                resource["selfLink"], projects, targets.gcp_region
            )
            if any(
                match_resource_identity(resource["selfLink"], identity, projects)
                for identity in state_ids
                if identity
            ):
                continue
            record = {
                "type": collection,
                "identity": resource["selfLink"],
                "url": resource["selfLink"],
                "native_id": resource["id"],
                "network": network_url,
                "network_id": str(network_id),
                "state_lineage": state["lineage"],
            }
            # Untracked NAT configuration requires its own review; discovering
            # the parent router is not approval to detach nested dependencies.
            if collection == "routers" and (
                resource.get("nats")
                or not resource["name"].startswith(targets.deployment + "-")
            ):
                pending.append(
                    record
                    | {"reason": "NAT/service router needs inspection; no force-detach"}
                )
            else:
                orphans.append(record)
    return orphans, pending


def inspect_teardown(targets: Targets) -> Teardown:
    session = targets.create_google_session()
    plan, state = plan_google_teardown(targets)
    orphans, pending = list_application_orphans(session, targets, state)
    tenant = state.get("outputs", {}).get("tenant", {}).get("value")
    users = []
    if tenant:
        response = session.get(
            f"https://identitytoolkit.googleapis.com/v2/projects/{targets.gcp_project}/tenants/{tenant}",
            timeout=30,
        )
        if response.status_code != 404:
            response.raise_for_status()
            if response.json().get("displayName") != targets.deployment:
                raise PermissionError("Application tenant ownership does not match")
            users = [
                {"subject": user["localId"]}
                for user in list_google_items(
                    session,
                    f"https://identitytoolkit.googleapis.com/v1/projects/{targets.gcp_project}/tenants/{tenant}/accounts:batchGet",
                    "users",
                    {"maxResults": 1000},
                    "nextPageToken",
                )
            ]
    return Teardown(
        plan,
        {
            "project": targets.gcp_project,
            "region": targets.gcp_region,
            "deployment": targets.deployment,
            "lineage": state["lineage"],
            "serial": state["serial"],
            "plan": str(plan.path),
            "terraform_changes": plan.build_inventory(),
            "external_resources": [
                *list_model_secrets(session, targets),
                *list_image_versions(session, targets),
                *orphans,
            ],
            "users": users,
            "retained": RETAINED_BOUNDARIES,
            "pending_networking": pending,
            "data_loss": "database/checkpoints, tenant users, model-key copies",
            "network_order": [
                "Owned orphan services before application destroy",
                "Policy cleanup requires immutable policy/network identity",
                "NGFW orphans require state recovery/import, not automatic detach",
                "Compute, disks, firewalls, policies, routers, then subnets",
                "Bounded native verification; unresolved dependencies stop cleanup",
            ],
        },
    )


def verify_external_resource(session: Any, targets: Targets, resource: dict) -> bool:
    url = resource["url"]
    projects = load_project_identities(targets, session)
    validate_resource_destination(url, projects, targets.gcp_region)
    response = session.get(url, timeout=30)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    metadata = response.json()
    if (
        resource.get("labels") is not None
        and metadata.get("labels", {}).get("deployment") != targets.deployment
    ):
        raise PermissionError("External resource ownership changed after review")
    if resource.get("native_id") and metadata.get("id") != resource["native_id"]:
        raise PermissionError("External native resource was replaced after review")
    if (
        resource.get("create_time")
        and metadata.get("createTime") != resource["create_time"]
    ):
        raise PermissionError("External resource was replaced after review")
    if resource.get("network"):
        network = metadata.get("network")
        if not match_resource_identity(network or "", resource["network"], projects):
            raise PermissionError("External network lineage changed after review")
        if resource.get("network_id"):
            response = session.get(resource["network"], timeout=30)
            response.raise_for_status()
            if str(response.json().get("id")) != resource["network_id"]:
                raise PermissionError("External network native identity changed")
    if resource.get("repository"):
        response = session.get(
            f"https://artifactregistry.googleapis.com/v1/{resource['repository']}",
            timeout=30,
        )
        response.raise_for_status()
        if response.json().get("labels", {}).get("deployment") != targets.deployment:
            raise PermissionError("Artifact repository ownership changed after review")
    return True


def delete_external_resources(
    session: Any, targets: Targets, resources: list[dict]
) -> list[dict]:
    order = {
        "services": 1,
        "instance": 2,
        "disk": 3,
        "firewalls": 4,
        "routers": 6,
        "subnetworks": 7,
        "networks": 8,
        "model_secret": 8,
        "image_version": 9,
    }
    pending = []
    # Docker builds publish indexes that reference image/attestation manifests.
    # Delete parents first; force removes tags, not manifest references.
    indexes = {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
    for resource in sorted(
        resources,
        key=lambda resource: (
            order[resource["type"]],
            resource.get("media_type") not in indexes,
            not bool(resource.get("subject_digest")),
        ),
    ):
        if not verify_external_resource(session, targets, resource):
            continue
        params = {}
        if resource["type"] == "image_version":
            params["force"] = "true"
        response = session.delete(
            resource["url"],
            params=params,
            timeout=30,
        )
        if response.status_code == 404:
            continue
        response.raise_for_status()
        operation = response.json() if response.content else {}
        if operation.get("name") and resource["type"] in (
            "image_version",
            "services",
        ):
            endpoint = resource["url"].split("/projects/", 1)[0]
            wait_for_operation(session, endpoint, operation, 300)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            response = session.get(resource["url"], timeout=30)
            if response.status_code == 404:
                break
            response.raise_for_status()
            time.sleep(3)
        else:
            pending.append(
                {
                    "identity": resource["identity"],
                    "status": "native deletion pending; re-inventory before retry",
                }
            )
            # Network dependency ordering must stop if an earlier delete remains.
            break
    return pending


def confirm_data_loss(
    targets: Targets, target: str | None, deployment: str | None, data_loss: str | None
) -> None:
    if (target, deployment, data_loss) != (
        targets.gcp_project,
        targets.deployment,
        DATA_LOSS_CONFIRMATION,
    ):
        raise PermissionError(
            "Exact project, deployment and data-loss confirmations are required"
        )


def destroy_google(
    targets: Targets,
    preview: bool,
    approval: Callable[[dict], None],
    presentation: Callable[[str, dict], None],
) -> None:
    teardown = inspect_teardown(targets)
    presentation("Reviewed GCP destruction scope", teardown.inventory)
    save_outputs("gcp-destroy", teardown.inventory | {"status": "preview"})
    if preview:
        return
    approval(teardown.inventory)
    if teardown.inventory["pending_networking"]:
        raise PermissionError("Unverified networking requires recovery before teardown")
    session = targets.create_google_session()
    verify_bootstrap(targets)
    state = load_state(targets, teardown.plan.directory)
    if (state["lineage"], state["serial"]) != (
        teardown.inventory["lineage"],
        teardown.inventory["serial"],
    ):
        raise PermissionError(
            "Application state changed after review; prepare a new destroy plan"
        )
    verify_destroy_scope(targets, teardown.plan, state)
    for resource in teardown.inventory["external_resources"]:
        verify_external_resource(session, targets, resource)
    evidence = teardown.inventory | {
        "status": "application destroy started; external cleanup pending"
    }
    save_outputs("gcp-destroy", evidence)
    try:
        dependencies = [
            resource
            for resource in teardown.inventory["external_resources"]
            if resource["type"] not in ("model_secret", "image_version")
            and resource.get("phase") != "after_application"
        ]
        pending = delete_external_resources(session, targets, dependencies)
        if pending:
            raise RuntimeError(
                "Orphan dependency deletion pending; application destruction stopped"
            )
        verify_destroy_scope(targets, teardown.plan, state)
        apply_reviewed_plan(targets, teardown.plan)
        remaining_state = load_state(targets, teardown.plan.directory)
        if list_state_resources(remaining_state):
            raise RuntimeError(
                "Application state still has managed resources; cleanup stopped"
            )
        pending = delete_external_resources(
            session,
            targets,
            [
                resource
                for resource in teardown.inventory["external_resources"]
                if resource["type"] in ("model_secret", "image_version")
                or resource.get("phase") == "after_application"
            ],
        )
        verify_bootstrap(targets)
        remaining = [
            resource["identity"]
            for resource in teardown.inventory["external_resources"]
            if verify_external_resource(session, targets, resource)
        ]
        # A fresh broad inventory detects newly discovered resources without
        # treating the review as permission to delete an expanded scope.
        orphans, networking = list_application_orphans(session, targets, state)
        remaining += [resource["identity"] for resource in orphans]
        remaining += [
            resource["identity"] for resource in list_model_secrets(session, targets)
        ]
        remaining += [
            resource["identity"] for resource in list_image_versions(session, targets)
        ]
        evidence |= {
            "remaining": sorted(set(remaining)),
            "pending_deletions": pending,
            "pending_networking": networking,
            "status": "incomplete; re-inventory"
            if remaining or pending or networking
            else "reviewed owned deletion confirmed; retained boundaries preserved",
        }
    except Exception:
        save_outputs(
            "gcp-destroy",
            evidence | {"status": "incomplete; inspect live resources before recovery"},
        )
        raise
    save_outputs("gcp-destroy", evidence)
    presentation("GCP teardown result", evidence)
