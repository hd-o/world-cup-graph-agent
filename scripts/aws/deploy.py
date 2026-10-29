import json
import shutil
import subprocess
import sys
from pathlib import Path
from scripts.aws.configuration import Targets
from scripts.aws.deployment import find_stack, load_deployment, read_tags
from scripts.shared.configuration import ROOT, save_outputs
from scripts.shared.enrollment import read_user_input
from scripts.shared.interface import confirm_action, show_inventory
from scripts.shared.invocations import (
    get_generated_path,
    get_invocation,
    write_metadata,
)
from typing import Any
from uuid import uuid4


def run_cdk(targets: Targets, arguments: list[str], environment: dict[str, str]) -> str:
    response = subprocess.run(
        ["cdk", *arguments],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    return response.stdout + response.stderr


def verify_database_replacement(
    targets: Targets, formation: Any, cdk_output: Path
) -> None:
    stack = find_stack(targets, formation)
    if stack is None:
        return
    deployed = formation.get_template(StackName=stack["StackId"])["TemplateBody"]
    if isinstance(deployed, str):
        deployed = json.loads(deployed)
    proposed = json.loads(
        (cdk_output / f"{targets.deployment}-aws.template.json").read_text()
    )
    protected = {"AWS::EC2::Instance", "AWS::EC2::Volume", "AWS::EC2::VolumeAttachment"}
    for name, resource in deployed["Resources"].items():
        if (
            resource["Type"] in protected
            and proposed["Resources"].get(name) != resource
        ):
            raise PermissionError(
                "Database compute/storage changes need a reviewed replacement plan"
            )


def protect_database_resources(targets: Targets, formation: Any) -> None:
    # A changed subnet can indirectly replace compute even with an identical
    # instance definition. CloudFormation must enforce this boundary too.
    formation.set_stack_policy(
        StackName=f"{targets.deployment}-aws",
        StackPolicyBody=json.dumps(
            {
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": "Update:*",
                        "Resource": "*",
                    },
                    {
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": ["Update:Replace", "Update:Delete"],
                        "Resource": "*",
                        "Condition": {
                            "StringEquals": {
                                "ResourceType": [
                                    "AWS::EC2::Instance",
                                    "AWS::EC2::Volume",
                                    "AWS::EC2::VolumeAttachment",
                                ]
                            }
                        },
                    },
                ]
            }
        ),
    )


def record_deployment_resources(outputs: dict) -> None:
    output_names_by_metadata_name = {
        "runtime_id": "RuntimeId",
        "database_instance_id": "DatabaseInstance",
        "vpc_id": "VpcId",
        "internet_gateway_id": "InternetGatewayId",
        "nat_gateway_id": "NatGatewayId",
        "runtime_security_group_id": "RuntimeSecurityGroupId",
        "database_security_group_id": "DatabaseSecurityGroupId",
    }
    write_metadata(
        get_invocation(),
        {
            metadata_name: outputs[output_name]
            for metadata_name, output_name in output_names_by_metadata_name.items()
        },
    )


def deploy_infrastructure(targets: Targets, preview: bool, execution: bool) -> None:
    environment = targets.build_aws_environment()
    cdk_output = get_generated_path("cdk.out")
    from scripts.shared.invocations import get_build_path

    environment |= {
        "DEPLOY_CDK_OUTPUT": str(cdk_output),
        "DEPLOY_AWS_ARTIFACT": str(get_build_path("aws.zip")),
    }
    formation = targets.create_aws_session().client("cloudformation")
    stack = find_stack(targets, formation)
    subprocess.run(
        ["python", "-m", "deploy.aws.app"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    verify_database_replacement(targets, formation, cdk_output)
    arguments = [
        f"{targets.deployment}-aws",
        "--app",
        str(cdk_output),
        "--output",
        str(cdk_output),
    ]
    differences = run_cdk(
        targets, ["diff", *arguments, "--change-set", "false"], environment
    )
    show_inventory("Synthesized AWS application differences", differences)
    if preview:
        return
    confirm_action(
        "Upload checked assets and prepare the CloudFormation change set?",
        execution=execution,
    )
    name = f"review-{uuid4().hex}"
    run_cdk(
        targets,
        [
            "deploy",
            *arguments,
            "--method",
            "prepare-change-set",
            "--change-set-name",
            name,
            "--require-approval",
            "never",
        ],
        environment,
    )
    # CDK prepares but never executes here; execution uses the exact reviewed ARN.
    try:
        change_set = formation.describe_change_set(
            StackName=f"{targets.deployment}-aws", ChangeSetName=name
        )
    except formation.exceptions.ClientError as error:
        if error.response["Error"]["Code"] == "ChangeSetNotFound":
            show_inventory("AWS application", {"status": "no infrastructure changes"})
            return
        raise
    changes = list(change_set.get("Changes", []))
    token = change_set.get("NextToken")
    while token:
        page = formation.describe_change_set(
            ChangeSetName=change_set["ChangeSetId"], NextToken=token
        )
        changes.extend(page.get("Changes", []))
        token = page.get("NextToken")
    if change_set["Status"] != "CREATE_COMPLETE":
        if "didn't contain changes" in change_set.get(
            "StatusReason", ""
        ) or "No updates are to be performed" in change_set.get("StatusReason", ""):
            show_inventory("AWS application", {"status": "no infrastructure changes"})
            return
        raise RuntimeError("CloudFormation change set is not ready; inspect status")
    if any(
        change["ResourceChange"].get("Replacement") in ("True", "Conditional")
        and change["ResourceChange"]["ResourceType"]
        in ("AWS::EC2::Instance", "AWS::EC2::Volume", "AWS::EC2::VolumeAttachment")
        for change in changes
    ):
        raise PermissionError(
            "CloudFormation proposes an uncertain database replacement"
        )
    evidence = {
        "account": targets.aws_account,
        "region": targets.aws_region,
        "deployment": targets.deployment,
        "change_set": change_set["ChangeSetId"],
        "stack": change_set["StackId"],
        "changes": changes,
        "status": "prepared; not executed",
    }
    show_inventory("Exact AWS infrastructure change set", evidence)
    save_outputs("aws-changes", evidence)
    confirm_action(
        "Execute this exact reviewed CloudFormation change set?", execution=execution
    )
    if stack is not None:
        protect_database_resources(targets, formation)
    formation.execute_change_set(ChangeSetName=change_set["ChangeSetId"])
    formation.get_waiter(
        "stack_update_complete" if stack else "stack_create_complete"
    ).wait(
        StackName=change_set["StackId"],
        WaiterConfig={"Delay": 10, "MaxAttempts": 180},
    )
    protect_database_resources(targets, formation)
    save_outputs("aws", load_deployment(targets))


def deploy_aws(
    targets: Targets,
    preview: bool = False,
    usernames: list[str] | None = None,
    bootstrap_approval: bool = False,
    infrastructure_approval: bool = False,
    provision_approval: bool = False,
    enrollment_approval: bool = False,
    build_timestamp: str | None = None,
) -> None:
    from scripts.aws.bootstrap import bootstrap_aws, inspect_bootstrap
    from scripts.aws.build import build_artifact
    from scripts.aws.enroll import enroll_users, validate_usernames
    from scripts.aws.inspect import verify_aws_entrypoint
    from scripts.aws.provision import provision_aws_database

    usernames = usernames if usernames is not None else ["alice", "bob"]
    validate_usernames(usernames)
    if not preview and not sys.stdin.isatty():
        approvals = {
            "bootstrap": bootstrap_approval,
            "infrastructure": infrastructure_approval,
            "provision": provision_approval,
            "enrollment": enrollment_approval or not usernames,
        }
        if not all(approvals.values()):
            show_inventory(
                "AWS deployment requires separate stage approvals",
                {
                    "required": [
                        name for name, approval in approvals.items() if not approval
                    ],
                    "action": "Supply each intended --approve-<stage> acknowledgement",
                },
            )
            raise PermissionError("Non-terminal deployment needs each stage approval")
    for program in ("aws", "cdk", "uv", "readelf", "docker"):
        if shutil.which(program) is None:
            raise ValueError(f"AWS deployment requires {program}")
    if not preview and any(
        not read_user_input(targets, username, "openrouter") for username in usernames
    ):
        raise ValueError(
            "Requested users need protected per-user OpenRouter deployment inputs"
        )
    targets.create_aws_session()
    stack = find_stack(targets)
    if stack is not None and stack["StackStatus"] not in (
        "CREATE_COMPLETE",
        "UPDATE_COMPLETE",
        "UPDATE_ROLLBACK_COMPLETE",
    ):
        raise PermissionError("Application stack is not in a deployable stable state")
    outputs = (
        {
            output["OutputKey"]: output["OutputValue"]
            for output in stack.get("Outputs", [])
        }
        if stack
        else {}
    )
    volumes = (
        targets.create_aws_session()
        .client("ec2")
        .describe_volumes(
            Filters=[{"Name": "tag:Deployment", "Values": [targets.deployment]}]
        )["Volumes"]
    )
    if any(
        volume["VolumeId"] != outputs.get("DatabaseVolume")
        and read_tags(volume)
        .get("aws:cloudformation:logical-id", "")
        .startswith("DatabaseData")
        for volume in volumes
    ):
        raise PermissionError(
            "Retained database storage needs a reviewed recovery attachment"
        )
    show_inventory(
        "AWS guided deployment",
        {
            "account": targets.aws_account,
            "region": targets.aws_region,
            "deployment": targets.deployment,
            "steps": [
                "Build and verify ARM64/Python 3.14",
                "Review retained bootstrap",
                "Review and execute exact infrastructure change set",
                "Configure private TLS database",
                "Enroll requested users",
                "Report connection readiness",
            ],
            "users": usernames,
            "preview": preview,
        },
    )
    from scripts.shared.invocations import (
        get_invocation,
        require_build,
        run_nested_invocation,
        select_build,
    )

    invocation = get_invocation()
    if build_timestamp is None:
        run_nested_invocation("build", "aws", invocation.timestamp, build_artifact)
        build_folder = require_build("aws", invocation.timestamp)
    else:
        build_folder = require_build("aws", build_timestamp)
    completed = []
    try:
        with select_build(build_folder):
            verify_aws_entrypoint()
            completed.append("artifact built and launcher verified")
            if preview:
                show_inventory(
                    "Retained bootstrap prerequisites", inspect_bootstrap(targets)
                )
                deploy_infrastructure(targets, True, False)
                show_inventory(
                    "Remaining proposed steps",
                    {
                        "database": (
                            "configure private TLS/seed after infrastructure approval"
                        ),
                        "users": usernames,
                    },
                )
                return
            bootstrap_aws(targets, bootstrap_approval)
            completed.append("retained bootstrap ready")
            deploy_infrastructure(targets, False, infrastructure_approval)
            record_deployment_resources(load_deployment(targets))
            completed.append("application infrastructure ready")
            provision_aws_database(targets, provision_approval)
            completed.append("private database ready")
            if usernames:
                enroll_users(targets, usernames, execution=enrollment_approval)
                completed.append("requested users enrolled")
            outputs = load_deployment(targets)
            save_outputs("aws", outputs)
            show_inventory("AWS readiness and public connection configuration", outputs)
    except BaseException:
        show_inventory(
            "AWS workflow stopped; no automatic rollback",
            {
                "completed": completed,
                "remaining": (
                    "Inspect status; completed cloud resources remain available"
                ),
            },
        )
        raise
