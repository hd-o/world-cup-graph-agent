import sys
import typer
from collections.abc import Callable
from pathlib import Path
from typing import Annotated


app = typer.Typer(
    help="Operate the GCP Cloud Run agent deployment.",
    no_args_is_help=False,
    pretty_exceptions_enable=False,
)


def present_inventory(title: str, payload: dict) -> None:
    from scripts.shared.interface import show_inventory

    show_inventory(title, payload)


def approve_operation(message: str, scope: dict, execution: bool = False) -> None:
    from scripts.shared.interface import confirm_action

    confirm_action(message, execution=execution)


def complete_targets(context: typer.Context):
    from scripts.gcp.configuration import load_targets
    from scripts.gcp.settings import load_gcp_settings
    from scripts.shared.interface import ask_text, show_inventory

    overrides = dict(context.obj or {})
    if sys.stdin.isatty():
        settings = load_gcp_settings(overrides)
        missing_names = settings.get_missing_routing_names()
        for name, environment_name in (
            (
                "google_cloud_project",
                "GOOGLE_CLOUD_PROJECT",
            ),
            ("gcp_region", "GCP_REGION"),
        ):
            if name in missing_names:
                overrides[name] = ask_text(environment_name)
    targets = load_targets(overrides)
    from scripts.shared.invocations import record_target

    record_target(targets, targets.gcp_project, targets.gcp_region)
    show_inventory(
        "Selected GCP destination",
        {
            "project": targets.gcp_project,
            "region": targets.gcp_region,
            "deployment": targets.deployment,
        },
    )
    return targets


def run_operation(operation: Callable[[], object]) -> None:
    from scripts.shared.interface import run_cli

    run_cli(operation)


def run_gcp_command(
    command: str,
    operation: Callable[[], object],
    timestamp: str | None = None,
    source_timestamp: str | None = None,
    preview: bool = False,
) -> None:
    from scripts.shared.invocations import open_invocation

    def execute_operation() -> None:
        with open_invocation(
            command, "gcp", timestamp, source_timestamp, preview
        ) as invocation:
            present_inventory(
                "Command artifacts",
                {"timestamp": invocation.timestamp, "folder": invocation.folder},
            )
            operation()

    run_operation(execute_operation)


def run_with_gcp_build(timestamp: str, operation: Callable[[], object]) -> object:
    from scripts.shared.invocations import bind_build, require_build

    bind_build(require_build("gcp", timestamp))
    return operation()


@app.callback(invoke_without_command=True)
def manage(
    context: typer.Context,
    project: Annotated[
        str | None, typer.Option(help="Override GOOGLE_CLOUD_PROJECT.")
    ] = None,
    region: Annotated[str | None, typer.Option(help="Override GCP_REGION.")] = None,
) -> None:
    context.obj = {
        name: value
        for name, value in (
            ("google_cloud_project", project),
            ("gcp_region", region),
        )
        if value is not None
    }
    if context.invoked_subcommand is None:
        print(context.get_help())


@app.command()
def build() -> None:
    """Build locked agent and gateway container bundles without cloud credentials."""

    def perform_build() -> None:
        from scripts.gcp.build import build_artifacts

        present_inventory("Local artifacts", {"artifacts": build_artifacts()})

    run_gcp_command("build", perform_build)


@app.command("status")
def inspect_status(context: typer.Context) -> None:
    """Inspect the authoritative remote application state."""

    def present_status() -> None:
        from scripts.gcp.inspect import inspect_deployment

        present_inventory(
            "GCP application state", inspect_deployment(complete_targets(context))
        )

    run_gcp_command("status", present_status)


@app.command()
def bootstrap(
    context: typer.Context,
    preview: bool = False,
    bootstrap_approval: Annotated[
        bool, typer.Option("--approve-bootstrap/--no-approve-bootstrap")
    ] = False,
) -> None:
    """Review retained APIs, backend and Identity Platform configuration."""

    def configure_bootstrap() -> None:
        from scripts.gcp.bootstrap import bootstrap_google, inspect_bootstrap

        targets = complete_targets(context)
        scope = inspect_bootstrap(targets)
        present_inventory("Retained bootstrap changes", scope)
        if not preview:
            approve_operation(
                "Configure retained GCP bootstrap", scope, bootstrap_approval
            )
            bootstrap_google(targets, scope)

    run_gcp_command("bootstrap", configure_bootstrap, preview=preview)


@app.command()
def publish(
    context: typer.Context,
    timestamp: Annotated[str, typer.Option(help="Build timestamp to publish.")],
    preview: bool = False,
    publication_approval: Annotated[
        bool, typer.Option("--approve-publish/--no-approve-publish")
    ] = False,
) -> None:
    """Review and publish immutable agent and gateway images."""

    def publish_artifact() -> None:
        from scripts.gcp.publish import inspect_publication, publish_images

        targets = complete_targets(context)
        scope = inspect_publication(targets)
        present_inventory("Agent and gateway publication", scope)
        if not preview:
            approve_operation(
                "Publish the reviewed agent and gateway artifacts",
                scope,
                publication_approval,
            )
            present_inventory("Published images", publish_images(targets, scope))

    run_gcp_command(
        "publish",
        lambda: run_with_gcp_build(timestamp, publish_artifact),
        source_timestamp=timestamp,
        preview=preview,
    )


@app.command()
def provision(
    context: typer.Context,
    provisioning_approval: Annotated[
        bool, typer.Option("--approve-provision/--no-approve-provision")
    ] = False,
) -> None:
    """Configure the existing private TLS database without resetting credentials."""

    def configure_database() -> None:
        from scripts.gcp.provision import provision_google_database

        targets = complete_targets(context)
        scope = {
            "database": targets.deployment + "-neo4j",
            "changes": [
                "Reuse/initialize protected TLS/accounts and bootstrap storage/access",
                "Configure private Neo4j/checkpoints/seed",
            ],
        }
        present_inventory("Private database configuration", scope)
        approve_operation(
            "Configure the private database", scope, provisioning_approval
        )
        provision_google_database(targets)

    run_gcp_command("provision", configure_database)


@app.command()
def enroll(
    context: typer.Context,
    users: str = "alice,bob",
    keyless: bool = False,
    enrollment_approval: Annotated[
        bool, typer.Option("--approve-enrollment/--no-approve-enrollment")
    ] = False,
) -> None:
    """Enroll requested users, preserving existing passwords and model secrets."""

    def enroll_accounts() -> None:
        from scripts.gcp.enroll import enroll_users

        targets = complete_targets(context)
        usernames = (
            ("keyless",)
            if keyless
            else tuple(name.strip() for name in users.split(",") if name.strip())
        )
        scope = {
            "users": list(usernames),
            "keyless": keyless,
            "policy": "Existing passwords and model keys are preserved",
        }
        present_inventory("Tenant enrollment", scope)
        approve_operation(
            "Enroll the requested tenant users", scope, enrollment_approval
        )
        enroll_users(targets, usernames, keyless)

    run_gcp_command("enroll", enroll_accounts)


def prompt_client_credentials(
    username: str | None, password_file: Path | None
) -> tuple[str, str]:
    from scripts.shared.interface import ask_password, ask_text

    login = username or ask_text("Email")
    if password_file:
        if password_file.stat().st_mode & 0o077:
            raise PermissionError("Password file must be protected (mode 0600)")
        password = password_file.read_text().rstrip("\r\n")
    else:
        password = ask_password("Password")
    return login, password


@app.command()
def converse(
    timestamp: Annotated[str, typer.Option(help="Successful deployment timestamp.")],
    username: str | None = None,
    password_file: Path | None = None,
    thread: str | None = None,
) -> None:
    """Converse using public client settings, without operator cloud credentials."""

    def open_conversation() -> None:
        from scripts.gcp.converse import converse_user, load_client_outputs
        from scripts.shared.interface import require_terminal

        require_terminal()
        outputs = load_client_outputs(timestamp)
        login, password = prompt_client_credentials(username, password_file)
        converse_user(login, password, outputs, thread)

    run_gcp_command("converse", open_conversation, source_timestamp=timestamp)


@app.command()
def inspect(
    timestamp: Annotated[str, typer.Option(help="Build timestamp to inspect.")],
) -> None:
    """Verify local artifact checksums without cloud credentials."""

    def inspect_local_artifacts() -> None:
        from scripts.gcp.inspect import inspect_artifacts

        present_inventory(
            "Verified local artifacts", {"artifacts": inspect_artifacts()}
        )

    run_gcp_command(
        "inspect",
        lambda: run_with_gcp_build(timestamp, inspect_local_artifacts),
        source_timestamp=timestamp,
    )


@app.command()
def stage(
    context: typer.Context,
    timestamp: Annotated[str, typer.Option(help="Build timestamp to stage.")],
    question: str = "What is Mbappe's birthplace?",
    fact: str = "Paris",
    staging_approval: Annotated[
        bool, typer.Option("--approve-staging/--no-approve-staging")
    ] = False,
) -> None:
    """Stage a pending native research approval; never approve tool execution."""

    def stage_approval() -> None:
        from scripts.gcp.stage import stage_research_approval

        targets = complete_targets(context)
        scope = {
            "question": question,
            "required_fact": fact,
            "policy": "No research or graph-write approval submitted",
        }
        present_inventory("Conversation approval staging", scope)
        approve_operation(
            "Create a conversation and stage its research approval",
            scope,
            staging_approval,
        )
        stage_research_approval(targets, question, fact)

    run_gcp_command(
        "stage",
        lambda: run_with_gcp_build(timestamp, stage_approval),
        source_timestamp=timestamp,
    )


@app.command()
def deploy(
    context: typer.Context,
    timestamp: Annotated[
        str | None,
        typer.Option(help="Use the successful build with this exact timestamp."),
    ] = None,
    preview: bool = False,
    users: str = "alice,bob",
    bootstrap_approval: Annotated[
        bool, typer.Option("--approve-bootstrap/--no-approve-bootstrap")
    ] = False,
    publication_approval: Annotated[
        bool, typer.Option("--approve-publish/--no-approve-publish")
    ] = False,
    database_plan_approval: Annotated[
        bool, typer.Option("--approve-database-plan/--no-approve-database-plan")
    ] = False,
    provisioning_approval: Annotated[
        bool, typer.Option("--approve-provision/--no-approve-provision")
    ] = False,
    application_plan_approval: Annotated[
        bool, typer.Option("--approve-application-plan/--no-approve-application-plan")
    ] = False,
    enrollment_approval: Annotated[
        bool, typer.Option("--approve-enrollment/--no-approve-enrollment")
    ] = False,
) -> None:
    """Guide database-first deployment with a separate approval for each change."""
    approvals = {
        "Configure retained project bootstrap": bootstrap_approval,
        "Publish the reviewed agent and gateway artifacts": publication_approval,
        "Apply the reviewed database prerequisite plan": database_plan_approval,
        "Configure the private database": provisioning_approval,
        "Apply the reviewed application plan": application_plan_approval,
        "Enroll the requested tenant users": enrollment_approval,
    }

    def deploy_application() -> None:
        from scripts.gcp.deploy import deploy_google

        if not sys.stdin.isatty() and not preview and not all(approvals.values()):
            raise ValueError(
                "Non-terminal deployment requires each action-specific approval"
            )
        deploy_google(
            complete_targets(context),
            lambda message, scope: approve_operation(
                message, scope, approvals[message]
            ),
            present_inventory,
            preview,
            tuple(name.strip() for name in users.split(",") if name.strip()),
            timestamp,
        )

    run_gcp_command(
        "deploy",
        deploy_application,
        timestamp=timestamp,
        source_timestamp=timestamp,
        preview=preview,
    )


def prompt_destroy_confirmations(
    targets, target: str | None, deployment: str | None, data_loss: str | None
) -> None:
    from scripts.gcp.destroy import DATA_LOSS_CONFIRMATION, confirm_data_loss
    from scripts.shared.interface import ask_text

    if sys.stdin.isatty():
        target = target or ask_text("Type the exact GCP project ID")
        deployment = deployment or ask_text("Type the exact deployment name")
        data_loss = data_loss or ask_text(f"Type {DATA_LOSS_CONFIRMATION}")
    confirm_data_loss(targets, target, deployment, data_loss)


@app.command()
def destroy(
    context: typer.Context,
    preview: bool = False,
    execution: Annotated[bool, typer.Option("--execute/--no-execute")] = False,
    target_confirmation: Annotated[str | None, typer.Option("--confirm-target")] = None,
    deployment_confirmation: Annotated[
        str | None, typer.Option("--confirm-deployment")
    ] = None,
    data_loss_confirmation: Annotated[
        str | None, typer.Option("--confirm-data-loss")
    ] = None,
) -> None:
    """Review the remote-state destroy plan and exactly confirm data loss."""

    def destroy_application() -> None:
        from scripts.gcp.destroy import confirm_data_loss as verify_confirmations
        from scripts.gcp.destroy import destroy_google

        targets = complete_targets(context)
        if not sys.stdin.isatty() and not preview:
            if not execution:
                raise ValueError("Non-terminal destruction requires --execute")
            verify_confirmations(
                targets,
                target_confirmation,
                deployment_confirmation,
                data_loss_confirmation,
            )
        destroy_google(
            targets,
            preview,
            lambda scope: prompt_destroy_confirmations(
                targets,
                target_confirmation,
                deployment_confirmation,
                data_loss_confirmation,
            ),
            present_inventory,
        )

    run_gcp_command("destroy", destroy_application, preview=preview)


if __name__ == "__main__":
    app()
