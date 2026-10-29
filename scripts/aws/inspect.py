import json
import os
import subprocess
import zipfile
from pathlib import Path
from scripts.aws.configuration import AWS_TELEMETRY
from scripts.shared.invocations import get_build_path
from tempfile import TemporaryDirectory


def verify_aws_entrypoint() -> None:
    with TemporaryDirectory(prefix="aws-entrypoint-", dir="/tmp/opencode") as temporary:
        folder = Path(temporary)
        with zipfile.ZipFile(get_build_path("aws.zip")) as archive:
            archive.extractall(folder)
            for member in archive.infolist():
                if not member.is_dir():
                    (folder / member.filename).chmod(
                        (member.external_attr >> 16) & 0o777
                    )
        probe = folder / "probe.py"
        probe.write_text(
            "#!/usr/bin/env python3\nimport runpy\n"
            "from world_cup_graph_agent.runtimes.agentcore import app\n"
            "app.run = lambda: print('Instrumented ARM64 serving launcher passed')\n"
            "runpy.run_path('/var/task/serve.py', run_name='__main__')\n"
        )
        probe.chmod(0o755)
        environment = AWS_TELEMETRY | {
            "AWS_REGION": "us-east-1",
            "AWS_EC2_METADATA_DISABLED": "true",
            "APPLICATION_LOG_GROUP": "/aws/bedrock-agentcore/probe/application",
        }
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                "linux/arm64",
                "--mount",
                f"type=bind,source={folder},target=/var/task,readonly",
                *[
                    argument
                    for name, value in environment.items()
                    for argument in ("-e", f"{name}={value}")
                ],
                "--entrypoint",
                "/var/task/opentelemetry-instrument",
                "public.ecr.aws/lambda/python:3.14-arm64",
                "/var/task/probe.py",
            ],
            env=os.environ,
            check=True,
            timeout=180,
            capture_output=True,
        )
    print(json.dumps({"aws_entrypoint": "passed", "managed_startup": "unverified"}))
