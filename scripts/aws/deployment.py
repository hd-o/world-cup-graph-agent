from scripts.aws.configuration import Targets
from typing import Any


def read_tags(resource: dict) -> dict[str, str]:
    return {tag["Key"]: tag["Value"] for tag in resource.get("Tags", [])}


def verify_stack_identity(stack: dict, targets: Targets) -> None:
    prefix = (
        f"arn:aws:cloudformation:{targets.aws_region}:{targets.aws_account}:"
        f"stack/{targets.deployment}-aws/"
    )
    if not stack["StackId"].startswith(prefix):
        raise PermissionError("CloudFormation stack destination does not match target")
    if read_tags(stack).get("Deployment") != targets.deployment:
        raise PermissionError("CloudFormation stack lacks deployment ownership")


def find_stack(
    targets: Targets, formation: Any = None, identity: str | None = None
) -> dict | None:
    formation = formation or targets.create_aws_session().client("cloudformation")
    try:
        stack = formation.describe_stacks(
            StackName=identity or f"{targets.deployment}-aws"
        )["Stacks"][0]
    except formation.exceptions.ClientError as error:
        if (
            error.response["Error"]["Code"] == "ValidationError"
            and "does not exist" in error.response["Error"]["Message"]
        ):
            return None
        raise
    verify_stack_identity(stack, targets)
    return stack


def load_deployment(targets: Targets) -> dict:
    stack = find_stack(targets)
    if stack is None or stack["StackStatus"] == "DELETE_COMPLETE":
        raise ValueError(
            "AWS application stack is absent; deploy before this operation"
        )
    return {
        output["OutputKey"]: output["OutputValue"]
        for output in stack.get("Outputs", [])
    } | {
        "account": targets.aws_account,
        "region": targets.aws_region,
        "deployment": targets.deployment,
        "stack": stack["StackId"],
    }


def list_stack_resources(formation: Any, identity: str) -> list[dict]:
    return [
        {
            "logical_identity": resource["LogicalResourceId"],
            "type": resource["ResourceType"],
            "identity": resource.get("PhysicalResourceId"),
            "status": resource["ResourceStatus"],
        }
        for page in formation.get_paginator("list_stack_resources").paginate(
            StackName=identity
        )
        for resource in page["StackResourceSummaries"]
    ]


def show_status(targets: Targets) -> None:
    from scripts.shared.interface import show_inventory

    formation = targets.create_aws_session().client("cloudformation")
    stack = find_stack(targets, formation)
    payload = {
        "account": targets.aws_account,
        "region": targets.aws_region,
        "deployment": targets.deployment,
    }
    if stack is not None:
        payload |= {
            "stack": stack["StackId"],
            "status": stack["StackStatus"],
            "resources": list_stack_resources(formation, stack["StackId"]),
            "outputs": {
                output["OutputKey"]: output["OutputValue"]
                for output in stack.get("Outputs", [])
            },
        }
    else:
        payload["status"] = "application stack absent"
    show_inventory("AWS deployment", payload)
