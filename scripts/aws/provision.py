import hashlib
import json
import time
from pathlib import Path
from scripts.aws.configuration import Targets
from scripts.aws.deployment import load_deployment
from scripts.shared.database import build_bootstrap_bundle, create_database_material
from scripts.shared.interface import confirm_action, show_inventory
from tempfile import TemporaryDirectory


def provision_aws_database(targets: Targets, execution: bool = False) -> None:
    session = targets.create_aws_session()
    outputs = load_deployment(targets)
    show_inventory(
        "AWS private database provisioning",
        {
            "account": targets.aws_account,
            "region": targets.aws_region,
            "deployment": targets.deployment,
            "instance": outputs["DatabaseInstance"],
            "volume": outputs["DatabaseVolume"],
            "changes": "Create missing TLS/passwords and run SSM configuration/seed",
            "preserved": "Graph, checkpoints and existing credentials",
        },
    )
    confirm_action("Configure the owned private database?", execution=execution)
    client = session.client("secretsmanager")
    try:
        material = json.loads(
            client.get_secret_value(SecretId=outputs["DatabaseBootstrapSecret"])[
                "SecretString"
            ]
        )
    except client.exceptions.ResourceNotFoundException:
        # CloudFormation owns the container; an absent value needs initial material.
        client.describe_secret(SecretId=outputs["DatabaseBootstrapSecret"])
        material = create_database_material()
        client.put_secret_value(
            SecretId=outputs["DatabaseBootstrapSecret"],
            SecretString=json.dumps(material),
        )
    runtime = {
        name: material[name]
        for name in ("reader_password", "writer_password", "ca_certificate")
    }
    client.put_secret_value(
        SecretId=outputs["DatabaseRuntimeSecret"], SecretString=json.dumps(runtime)
    )
    bucket = f"cdk-hnb659fds-assets-{targets.aws_account}-{targets.aws_region}"
    storage = session.client("s3")
    with TemporaryDirectory(
        prefix="database-bundle-", dir="/tmp/opencode"
    ) as temporary:
        bundle = build_bootstrap_bundle(Path(temporary))
        with bundle.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        bundle_key = f"{targets.deployment}/bootstrap/{checksum}.tar.gz"
        storage.upload_file(
            str(bundle),
            bucket,
            bundle_key,
            ExtraArgs={"ExpectedBucketOwner": targets.aws_account},
        )
    commands = [
        "set -eu",
        "umask 077",
        "mkdir -p /opt/worldcup/bootstrap /opt/worldcup/dependencies",
        "dnf install -y awscli-2 xfsprogs docker",
        "systemctl enable --now docker",
        f"aws s3 cp s3://{bucket}/{bundle_key} "
        "/opt/worldcup/bootstrap.tar.gz --only-show-errors",
        "tar -xzf /opt/worldcup/bootstrap.tar.gz -C /opt/worldcup/bootstrap",
        "docker run --rm -v /opt/worldcup/bootstrap:/installation:ro "
        "-v /opt/worldcup/dependencies:/dependencies python:3.14.4-slim "
        "python -m pip install --quiet --target /dependencies "
        "-r /installation/requirements.txt",
        f"aws secretsmanager get-secret-value --region {targets.aws_region} "
        f"--secret-id {targets.deployment}/database-bootstrap --query SecretString "
        "--output text > /opt/worldcup/bootstrap.json",
        f"python3 /opt/worldcup/bootstrap/configure.py {outputs['DatabaseVolume']}",
    ]
    management = session.client("ssm")
    response = management.send_command(
        InstanceIds=[outputs["DatabaseInstance"]],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands, "executionTimeout": ["900"]},
        Comment=f"{targets.deployment} private database provisioning",
    )
    command_id = response["Command"]["CommandId"]
    deadline = time.monotonic() + 960
    while time.monotonic() < deadline:
        try:
            invocation = management.get_command_invocation(
                CommandId=command_id, InstanceId=outputs["DatabaseInstance"]
            )
        except management.exceptions.InvocationDoesNotExist:
            time.sleep(3)
            continue
        status = invocation["Status"]
        if status == "Success":
            show_inventory(
                "AWS database readiness",
                {
                    "status": (
                        "private TLS, accounts, checkpoint metadata and seed ready"
                    ),
                    "command": command_id,
                },
            )
            return
        if status in ("Failed", "Cancelled", "TimedOut"):
            raise RuntimeError(
                f"Database provisioning {status}; inspect SSM command {command_id}"
            )
        time.sleep(3)
    raise TimeoutError(
        f"Database provisioning pending; inspect SSM command {command_id}"
    )
