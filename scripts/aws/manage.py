import sys
import typer
from collections.abc import Callable
from pathlib import Path
from scripts.shared.interface import ask_text, run_cli, show_inventory
from typing import TYPE_CHECKING, Annotated


if TYPE_CHECKING:
    from scripts.aws.configuration import Targets


application = typer.Typer(
    help="AWS deployment operator and public conversation client.",
    no_args_is_help=False,
    pretty_exceptions_enable=False,
)


def load_operation_targets(context: typer.Context) -> Targets:
    from scripts.aws.configuration import load_targets
    from scripts.aws.settings import load_aws_settings

    overrides = dict(context.obj or {})
    if sys.stdin.isatty():
        settings = load_aws_settings(overrides)
        missing_names = settings.get_missing_routing_names()
        for name, message in (
            ("aws_profile", "AWS profile"),
            ("aws_account_id", "AWS account (12 digits)"),
            ("aws_region", "AWS region"),
        ):
            if name in missing_names:
                overrides[name] = ask_text(message)
    targets = load_targets(overrides)
    from scripts.shared.invocations import record_target

    record_target(targets, targets.aws_account, targets.aws_region)
    return targets


def run_aws_command(
    command: str,
    operation: Callable[[], object],
    timestamp: str | None = None,
    source_timestamp: str | None = None,
    preview: bool = False,
) -> None:
    from scripts.shared.invocations import open_invocation

    def run_operation() -> None:
        with open_invocation(
            command, "aws", timestamp, source_timestamp, preview
        ) as invocation:
            show_inventory(
                "Command artifacts",
                {"timestamp": invocation.timestamp, "folder": invocation.folder},
            )
            operation()

    run_cli(run_operation)


def select_aws_build(timestamp: str) -> None:
    from scripts.shared.invocations import bind_build, require_build

    bind_build(require_build("aws", timestamp))


def run_with_aws_build(timestamp: str, operation: Callable[[], object]) -> object:
    select_aws_build(timestamp)
    return operation()


@application.callback(invoke_without_command=True)
def manage(
    context: typer.Context,
    profile: Annotated[str | None, typer.Option(help="Override AWS_PROFILE.")] = None,
    account: Annotated[
        str | None, typer.Option(help="Override AWS_ACCOUNT_ID.")
    ] = None,
    region: Annotated[str | None, typer.Option(help="Override AWS_REGION.")] = None,
) -> None:
    context.obj = {
        name: value
        for name, value in {
            "aws_profile": profile,
            "aws_account_id": account,
            "aws_region": region,
        }.items()
        if value is not None
    }
    if context.invoked_subcommand is None:
        print(context.get_help())


@application.command()
def build() -> None:
    """Build the direct ARM64/Python 3.14 artifact without cloud authentication."""
    from scripts.aws.build import build_artifact

    run_aws_command("build", build_artifact)


@application.command("status")
def inspect_status(context: typer.Context) -> None:
    """Discover current CloudFormation metadata and resource inventory."""
    from scripts.aws.deployment import show_status

    run_aws_command("status", lambda: show_status(load_operation_targets(context)))


@application.command()
def bootstrap(
    context: typer.Context,
    execution: Annotated[
        bool, typer.Option("--execute", help="Acknowledge retained bootstrap changes.")
    ] = False,
) -> None:
    """Inspect and configure retained CDK/bootstrap and shared tracing prerequisites."""
    from scripts.aws.bootstrap import bootstrap_aws

    run_aws_command(
        "bootstrap", lambda: bootstrap_aws(load_operation_targets(context), execution)
    )


@application.command()
def deploy(
    context: typer.Context,
    timestamp: Annotated[
        str | None,
        typer.Option(help="Use the successful build with this exact timestamp."),
    ] = None,
    preview: Annotated[
        bool, typer.Option(help="Build, verify and review; do not apply cloud changes.")
    ] = False,
    bootstrap_approval: Annotated[
        bool, typer.Option("--approve-bootstrap", help="Approve retained bootstrap.")
    ] = False,
    infrastructure_approval: Annotated[
        bool, typer.Option("--approve-infrastructure", help="Approve checked changes.")
    ] = False,
    provision_approval: Annotated[
        bool, typer.Option("--approve-provision", help="Approve private DB setup.")
    ] = False,
    enrollment_approval: Annotated[
        bool, typer.Option("--approve-enrollment", help="Approve per-user enrollment.")
    ] = False,
    user: Annotated[
        list[str] | None,
        typer.Option(help="Requested demo username; repeat for multiple users."),
    ] = None,
) -> None:
    """Guide build, reviewed infrastructure, private database and enrollment."""
    from scripts.aws.deploy import deploy_aws

    run_aws_command(
        "deploy",
        lambda: deploy_aws(
            load_operation_targets(context),
            preview,
            user,
            bootstrap_approval,
            infrastructure_approval,
            provision_approval,
            enrollment_approval,
            timestamp,
        ),
        timestamp=timestamp,
        source_timestamp=timestamp,
        preview=preview,
    )


@application.command()
def provision(
    context: typer.Context,
    execution: Annotated[
        bool,
        typer.Option(
            "--execute",
            help="Acknowledge database configuration and bootstrap publication.",
        ),
    ] = False,
) -> None:
    """Configure private Neo4j TLS, accounts, schemas and seed with preserved data."""
    from scripts.aws.provision import provision_aws_database

    run_aws_command(
        "provision",
        lambda: provision_aws_database(load_operation_targets(context), execution),
    )


@application.command()
def enroll(
    context: typer.Context,
    user: Annotated[
        list[str] | None, typer.Option(help="Username; repeat for multiple users.")
    ] = None,
    keyless: Annotated[
        bool, typer.Option(help="Create a keyless acceptance identity.")
    ] = False,
    execution: Annotated[
        bool,
        typer.Option(
            "--execute",
            help="Acknowledge user enrollment; existing credentials remain unchanged.",
        ),
    ] = False,
) -> None:
    """Enroll requested users with protected per-user credentials."""
    from scripts.aws.enroll import enroll_users

    run_aws_command(
        "enroll",
        lambda: enroll_users(load_operation_targets(context), user, keyless, execution),
    )


@application.command()
def converse(
    timestamp: Annotated[str, typer.Option(help="Successful deployment timestamp.")],
    username: Annotated[
        str | None, typer.Option(help="Username; password uses protected inputs.")
    ] = None,
    thread: Annotated[
        str | None, typer.Option(help="Existing conversation ID.")
    ] = None,
    request: Annotated[
        Path | None,
        typer.Option(help="Explicit request JSON for non-interactive use; no replay."),
    ] = None,
    execution: Annotated[
        bool, typer.Option("--execute", help="Acknowledge mutating client request.")
    ] = False,
) -> None:
    """Log in and converse without AWS operator credentials or printing tokens."""
    from scripts.aws.converse import converse_user

    run_aws_command(
        "converse",
        lambda: converse_user(
            timestamp,
            username,
            thread,
            request,
            execution,
        ),
        source_timestamp=timestamp,
    )


@application.command()
def inspect(
    timestamp: Annotated[str, typer.Option(help="Build timestamp to inspect.")],
) -> None:
    """Verify the instrumented ARM64 serving entrypoint using Docker."""
    from scripts.aws.inspect import verify_aws_entrypoint

    run_aws_command(
        "inspect",
        lambda: run_with_aws_build(timestamp, verify_aws_entrypoint),
        source_timestamp=timestamp,
    )


@application.command()
def stage(
    context: typer.Context,
    timestamp: Annotated[str, typer.Option(help="Build timestamp to stage.")],
    question: Annotated[
        str, typer.Option(help="Question requiring a genuine graph gap.")
    ] = "What is Mbappe's birthplace?",
    fact: Annotated[
        str, typer.Option(help="Expected research fact for the protected probe report.")
    ] = "Paris",
    execution: Annotated[
        bool,
        typer.Option(
            "--execute",
            help=(
                "Acknowledge model calls for staging only; no research/write approval."
            ),
        ),
    ] = False,
) -> None:
    """Stage a pending native research approval without approving research or writes."""
    from scripts.aws.stage import stage_research_approval

    run_aws_command(
        "stage",
        lambda: run_with_aws_build(
            timestamp,
            lambda: stage_research_approval(
                load_operation_targets(context), question, fact, execution
            ),
        ),
        source_timestamp=timestamp,
    )


@application.command()
def destroy(
    context: typer.Context,
    execution: Annotated[
        bool, typer.Option("--execute", help="Acknowledge destructive execution.")
    ] = False,
    preview: Annotated[
        bool, typer.Option(help="Inventory only; never delete resources.")
    ] = False,
    confirm_target: Annotated[
        str | None, typer.Option(help="Exact AWS account confirmation.")
    ] = None,
    confirm_deployment: Annotated[
        str | None, typer.Option(help="Exact deployment confirmation.")
    ] = None,
    confirm_data_loss: Annotated[
        str | None,
        typer.Option(help="Exact DELETE_DATABASE_USERS_AND_KEYS acknowledgement."),
    ] = None,
) -> None:
    """Review owned deletion; exit 2 reports pending AgentCore interface cleanup."""
    from scripts.aws.destroy import CleanupPending, destroy_aws

    if not sys.stdin.isatty() and not preview and not execution:
        show_inventory(
            "Destruction requires explicit execution",
            {
                "action": "Use --preview or --execute with exact confirmation flags",
            },
        )
        raise typer.Exit(1)

    def destroy_application() -> None:
        try:
            destroy_aws(
                load_operation_targets(context),
                preview,
                confirm_target,
                confirm_deployment,
                confirm_data_loss,
            )
        except CleanupPending:
            # Exit 2 distinguishes expected unfinished cleanup from success (0)
            # and operational failure (1); the operation presents safe guidance.
            raise typer.Exit(2) from None

    run_aws_command("destroy", destroy_application, preview=preview)


if __name__ == "__main__":
    application()
