import json
import shutil
from collections.abc import Callable
from pathlib import Path
from scripts.gcp.configuration import Targets
from scripts.gcp.deployment import (
    Plan,
    apply_reviewed_plan,
    initialize_backend,
    inspect_application_state,
    list_state_resources,
    load_existing_state,
    load_saved_plan,
    prepare_application_workspace,
    run_terraform,
    save_private_file,
)
from scripts.shared.configuration import load_model_environment, save_outputs


DATABASE_TARGETS = (
    "google_compute_instance.database",
    "google_compute_firewall.bolt",
    "google_compute_firewall.operator",
    "google_secret_manager_secret.database",
    "google_secret_manager_secret_iam_member.bootstrap",
    "google_dns_record_set.database",
)


def record_deployment_resources(outputs: dict) -> None:
    from scripts.shared.invocations import get_invocation, write_metadata

    output_names_by_metadata_name = {
        "runtime_id": "runtime",
        "database_instance_id": "database_instance_id",
        "vpc_id": "vpc_id",
        "nat_gateway_id": "nat_gateway_id",
        "agent_service_id": "agent_service_id",
        "gateway_service_id": "gateway_service_id",
        "firewall_policy_id": "firewall_policy_id",
        "database_firewall_id": "database_firewall_id",
    }
    resources = {
        metadata_name: outputs[output_name]
        for metadata_name, output_name in output_names_by_metadata_name.items()
        if output_name in outputs
    }
    if resources:
        write_metadata(get_invocation(), resources)


def write_deployment_variables(
    targets: Targets,
    gateway_image: str,
    agent_image: str,
    model_environment: dict[str, str] | None = None,
    database_machine: str | None = None,
    directory: Path | None = None,
) -> Path:
    from scripts.shared.invocations import get_generated_path

    path = (directory or get_generated_path("private")) / "variables.tfvars.json"
    variables = {
        "project": targets.gcp_project,
        "region": targets.gcp_region,
        "deployment": targets.deployment,
        "gateway_image": gateway_image,
        "agent_image": agent_image,
        "model_environment": model_environment or {},
    }
    if database_machine:
        variables["database_machine"] = database_machine
    save_private_file(path, json.dumps(variables))
    return path


def plan_application(
    targets: Targets, gateway_image: str, agent_image: str, database: bool = False
) -> Plan:
    folder = prepare_application_workspace(
        "gcp-database" if database else "gcp-deployment"
    )
    initialize_backend(targets, folder)
    load_existing_state(targets, folder)
    configuration = write_deployment_variables(
        targets, gateway_image, agent_image, load_model_environment(), directory=folder
    )
    path = folder / "deployment.tfplan"
    run_terraform(
        targets,
        [
            "plan",
            "-input=false",
            f"-var-file={configuration}",
            f"-out={path}",
            *(
                [f"-target={address}" for address in DATABASE_TARGETS]
                if database
                else []
            ),
        ],
        folder,
    )
    plan = load_saved_plan(targets, path, folder)
    if any(
        "delete" in change["change"]["actions"]
        or "forget" in change["change"]["actions"]
        for change in plan.changes
    ):
        raise PermissionError(
            "Deployment deletes/replaces/forgets resources; recovery review required"
        )
    return plan


def deploy_google(
    targets: Targets,
    approval: Callable[[str, dict], None],
    presentation: Callable[[str, dict], None],
    preview: bool = False,
    usernames: tuple[str, ...] = ("alice", "bob"),
    build_timestamp: str | None = None,
) -> None:
    from scripts.gcp.bootstrap import (
        bootstrap_google,
        inspect_bootstrap,
        verify_bootstrap,
    )
    from scripts.gcp.build import build_artifacts
    from scripts.gcp.deployment import load_deployment_outputs
    from scripts.gcp.enroll import enroll_users
    from scripts.gcp.provision import provision_google_database
    from scripts.gcp.publish import inspect_publication, publish_images
    from scripts.shared.enrollment import read_user_input
    from scripts.shared.invocations import (
        bind_build,
        get_invocation,
        require_build,
        run_nested_invocation,
    )

    for command in ("terraform", "gcloud", "uv", "docker"):
        if shutil.which(command) is None:
            raise FileNotFoundError(f"Required tool is unavailable: {command}")
    targets.create_google_session()
    if not preview and any(
        not read_user_input(targets, username, "openrouter") for username in usernames
    ):
        raise ValueError(
            "Each requested user requires a protected per-user OpenRouter input"
        )
    invocation = get_invocation()
    if build_timestamp is None:
        run_nested_invocation("build", "gcp", invocation.timestamp, build_artifacts)
        build_folder = require_build("gcp", invocation.timestamp)
    else:
        build_folder = require_build("gcp", build_timestamp)
    bind_build(build_folder)
    presentation(
        "Selected GCP destination",
        {
            "project": targets.gcp_project,
            "region": targets.gcp_region,
            "deployment": targets.deployment,
            "steps": [
                "build",
                "retained bootstrap",
                "agent and gateway publication",
                "database Terraform plan",
                "private database configuration",
                "application Terraform plan",
                "tenant enrollment",
            ],
        },
    )
    state: dict | None = None
    inspection = inspect_bootstrap(targets)
    if not inspection["create_backend"]:
        state = inspect_application_state(targets)
    from scripts.gcp.inspect import inspect_artifacts

    presentation("Local serving artifacts", {"artifacts": inspect_artifacts()})
    completed: list[str] = ["local builds"]
    try:
        presentation("Retained bootstrap changes", inspection)
        changes = any(
            inspection[name]
            for name in (
                "enable_services",
                "initialize_identity_platform",
                "enable_tenants",
                "create_backend",
            )
        )
        if changes:
            if preview:
                presentation(
                    "Preview prerequisites",
                    {"blocked": "Approve bootstrap prerequisites; no cloud writes"},
                )
                return
            approval("Configure retained project bootstrap", inspection)
            bootstrap_google(targets, inspection)
        verify_bootstrap(targets)
        completed.append("retained bootstrap verified")
        publication = inspect_publication(targets)
        presentation("Agent and gateway publication scope", publication)
        if preview:
            state = inspect_application_state(targets)
            from scripts.gcp.handover import read_state_settings

            settings = read_state_settings(state or {})
            gateway_image = settings.get("gateway_image")
            agent_image = settings.get("agent_image")
            if not gateway_image or not agent_image:
                presentation(
                    "Preview prerequisites",
                    {"blocked": "Publish an immutable image first; no cloud writes"},
                )
                return
        else:
            approval("Publish the reviewed agent and gateway artifacts", publication)
            images = publish_images(targets, publication)
            gateway_image = images["gateway_image"]
            agent_image = images["agent_image"]
            completed.append("agent and gateway artifacts published")
        database_plan = plan_application(
            targets, gateway_image, agent_image, database=True
        )
        scope = {
            "plan": str(database_plan.path),
            "changes": database_plan.build_inventory(),
            "notice": "Database-first staging includes dependent networking and IAM",
        }
        presentation("Actual database prerequisite plan", scope)
        if preview:
            if check_database_preview_readiness(database_plan, state):
                application_plan = plan_application(targets, gateway_image, agent_image)
                presentation(
                    "Actual application Terraform plan",
                    {
                        "plan": str(application_plan.path),
                        "changes": application_plan.build_inventory(),
                        "policy": "Preview only; no provisioning or application writes",
                    },
                )
                return
            presentation(
                "Preview boundary",
                {"blocked": "Database apply/TLS setup must precede agent startup"},
            )
            return
        approval("Apply the reviewed database prerequisite plan", scope)
        apply_reviewed_plan(targets, database_plan)
        record_deployment_resources(load_deployment_outputs(targets))
        completed.append("database infrastructure applied")
        scope = {
            "operations": [
                "reuse or initialize protected TLS/accounts",
                "configure private Neo4j/checkpoints/seed",
                "upload protected bootstrap bundle",
                "grant database identity bootstrap-object access",
            ],
            "database": targets.deployment + "-neo4j",
        }
        presentation("Private database configuration", scope)
        approval("Configure the private database", scope)
        provision_google_database(targets)
        completed.append("private database configured")
        application_plan = plan_application(targets, gateway_image, agent_image)
        scope = {
            "plan": str(application_plan.path),
            "changes": application_plan.build_inventory(),
        }
        presentation("Actual application Terraform plan", scope)
        approval("Apply the reviewed application plan", scope)
        apply_reviewed_plan(targets, application_plan)
        outputs = load_deployment_outputs(targets)
        record_deployment_resources(outputs)
        completed.append("managed application infrastructure applied")
        if usernames:
            scope = {
                "users": list(usernames),
                "policy": "Existing passwords, model keys and graph data are preserved",
            }
            presentation("Tenant enrollment", scope)
            approval("Enroll the requested tenant users", scope)
            enroll_users(targets, usernames)
            completed.append("requested tenant users enrolled")
        save_outputs("gcp", outputs)
        presentation(
            "Readiness",
            {name: value for name, value in outputs.items() if name != "login_key"},
        )
    finally:
        # Cancellation/failure never implies rollback of a completed cloud step.
        presentation(
            "Completed GCP steps; resources remain available for inspection",
            {"completed": completed},
        )


def check_database_preview_readiness(plan: Plan, state: dict | None) -> bool:
    changes = [change for change in plan.changes if change.get("mode") != "data"]
    if any(change["change"]["actions"] != ["no-op"] for change in changes):
        return False
    database = next(
        (
            resource["values"]
            for resource in list_state_resources(state or {})
            if resource["address"] == "google_compute_instance.database"
        ),
        None,
    )
    if database is None or database.get("current_status") != "RUNNING":
        return False
    refreshed = next(
        (
            change["change"].get("before")
            for change in changes
            if change["address"] == "google_compute_instance.database"
        ),
        None,
    )
    return refreshed is not None and refreshed.get("current_status") == "RUNNING"
