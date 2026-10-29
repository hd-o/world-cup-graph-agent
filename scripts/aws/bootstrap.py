import json
import subprocess
from scripts.aws.configuration import Targets
from scripts.shared.configuration import ROOT, save_outputs
from scripts.shared.interface import confirm_action, show_inventory


POLICY_NAME = "worldcup-bootstrap-transaction-search"


def inspect_bootstrap(targets: Targets) -> dict:
    session = targets.create_aws_session()
    formation = session.client("cloudformation")
    try:
        stack = formation.describe_stacks(StackName="CDKToolkit")["Stacks"][0]
    except formation.exceptions.ClientError as error:
        if (
            error.response["Error"]["Code"] != "ValidationError"
            or "does not exist" not in error.response["Error"]["Message"]
        ):
            raise
        stack = None
    policies = session.client("logs").describe_resource_policies()["resourcePolicies"]
    destination = (
        session.client("xray")
        .get_trace_segment_destination()
        .get("Destination", "XRay")
    )
    return {
        "account": targets.aws_account,
        "region": targets.aws_region,
        "deployment": targets.deployment,
        "cdk_stack": stack["StackStatus"]
        if stack
        else "create CDKToolkit and shared asset bucket",
        "transaction_search_policy": "present"
        if any(policy["policyName"] == POLICY_NAME for policy in policies)
        else "create shared XRay log policy",
        "trace_destination": destination,
        "proposed_trace_destination": "CloudWatchLogs",
        "retained": "CDKToolkit, shared storage and tracing survive teardown",
    }


def bootstrap_aws(targets: Targets, execution: bool = False) -> None:
    inventory = inspect_bootstrap(targets)
    show_inventory("AWS retained bootstrap prerequisites and changes", inventory)
    if (
        inventory["cdk_stack"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE")
        and inventory["transaction_search_policy"] == "present"
        and inventory["trace_destination"] == "CloudWatchLogs"
    ):
        return
    confirm_action(
        "Configure retained AWS bootstrap and shared tracing?", execution=execution
    )
    session = targets.create_aws_session()
    subprocess.run(
        [
            "cdk",
            "bootstrap",
            f"aws://{targets.aws_account}/{targets.aws_region}",
            "--app",
            "",
        ],
        cwd=ROOT,
        env=targets.build_aws_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    client = session.client("logs")
    policies = client.describe_resource_policies()["resourcePolicies"]
    if not any(policy["policyName"] == POLICY_NAME for policy in policies):
        client.put_resource_policy(
            policyName=POLICY_NAME,
            policyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "TransactionSearchXRayAccess",
                            "Effect": "Allow",
                            "Principal": {"Service": "xray.amazonaws.com"},
                            "Action": "logs:PutLogEvents",
                            "Resource": [
                                f"arn:aws:logs:{targets.aws_region}:{targets.aws_account}:log-group:aws/spans:*",
                                f"arn:aws:logs:{targets.aws_region}:{targets.aws_account}:log-group:/aws/application-signals/data:*",
                            ],
                            "Condition": {
                                "StringEquals": {
                                    "aws:SourceAccount": targets.aws_account
                                }
                            },
                        }
                    ],
                }
            ),
        )
    tracing = session.client("xray")
    if (
        tracing.get_trace_segment_destination().get("Destination", "XRay")
        != "CloudWatchLogs"
    ):
        tracing.update_trace_segment_destination(Destination="CloudWatchLogs")
    save_outputs(
        "aws-bootstrap",
        {
            "account": targets.aws_account,
            "region": targets.aws_region,
            "stack": "CDKToolkit",
            "transaction_search_policy": POLICY_NAME,
            "trace_destination": "CloudWatchLogs",
        },
    )
