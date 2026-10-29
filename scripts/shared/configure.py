import argparse
import json
import os
import socket
import ssl
import subprocess
import time
from pathlib import Path


ROOT = Path("/opt/worldcup")
DATA = Path("/srv/worldcup")


def run_command(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True, timeout=600)


def write_private_file(path: Path, content: str, owner: int = 0) -> None:
    path.write_text(content)
    path.chmod(0o600)
    os.chown(path, owner, owner)


def configure_database(volume_id: str) -> None:
    cloud = "aws" if volume_id.startswith("vol-") else "gcp"
    device_name = (
        f"nvme-Amazon_Elastic_Block_Store_{volume_id.replace('-', '')}"
        if cloud == "aws"
        else "google-worldcup-data"
    )
    device = Path("/dev/disk/by-id") / device_name
    image = "python:3.14.4-slim"
    deadline = time.monotonic() + 120
    while not device.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("Attached database data volume is unavailable")
        time.sleep(2)
    filesystem = subprocess.run(
        ["lsblk", "-no", "FSTYPE", str(device.resolve())],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not filesystem:
        run_command(["mkfs.xfs", str(device)])
    elif filesystem != "xfs":
        raise ValueError("Data volume has an unexpected filesystem; refusing to format")
    DATA.mkdir(parents=True, exist_ok=True)
    if not os.path.ismount(DATA):
        run_command(["mount", str(device), str(DATA)])
    mounts = Path("/etc/fstab")
    mount = f"{device} {DATA} xfs defaults 0 2\n"
    if mount not in mounts.read_text():
        with mounts.open("a") as stream:
            stream.write(mount)

    # The dedicated database host must not restart Docker against the root
    # filesystem directory when its persistent disk is absent or still mounting.
    dependency = Path("/etc/systemd/system/docker.service.d")
    dependency.mkdir(parents=True, exist_ok=True)
    (dependency / "worldcup.conf").write_text(
        "[Unit]\nRequiresMountsFor=/srv/worldcup\n"
    )
    run_command(["systemctl", "daemon-reload"])

    material = json.loads((ROOT / "bootstrap.json").read_text())
    certificates = DATA / "certificates"
    configuration = DATA / "configuration"
    graph = DATA / "data"
    for folder in (certificates, configuration, graph):
        folder.mkdir(exist_ok=True)
        os.chown(folder, 7474, 7474)
    write_private_file(
        certificates / "private.key", material["server_private_key"], 7474
    )
    write_private_file(
        certificates / "public.crt", material["server_certificate"], 7474
    )
    write_private_file(ROOT / "ca.pem", material["ca_certificate"])
    write_private_file(
        configuration / "neo4j.conf",
        """server.bolt.listen_address=0.0.0.0:7687
server.bolt.tls_level=REQUIRED
server.http.enabled=false
server.https.enabled=false
dbms.ssl.policy.bolt.enabled=true
dbms.ssl.policy.bolt.base_directory=/certificates
dbms.ssl.policy.bolt.private_key=private.key
dbms.ssl.policy.bolt.public_certificate=public.crt
dbms.ssl.policy.bolt.client_auth=NONE
server.memory.heap.initial_size=512m
server.memory.heap.max_size=768m
server.memory.pagecache.size=768m
db.memory.transaction.max=64MiB
db.transaction.timeout=30s
""",
        7474,
    )
    authentication = ROOT / "database.env"
    write_private_file(
        authentication,
        f"NEO4J_AUTH=neo4j/{material['bootstrap_password']}\nNEO4J_ACCEPT_LICENSE_AGREEMENT=yes\n",
    )
    existing = subprocess.run(
        ["docker", "inspect", "worldcup-neo4j"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if existing.returncode == 0:
        run_command(["docker", "start", "worldcup-neo4j"])
    else:
        run_command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                "worldcup-neo4j",
                "--restart",
                "unless-stopped",
                "--memory",
                "2800m",
                "--log-opt",
                "max-size=10m",
                "--log-opt",
                "max-file=3",
                "-p",
                "7687:7687",
                "--env-file",
                str(authentication),
                "-v",
                f"{graph}:/data",
                "-v",
                f"{configuration}:/conf:ro",
                "-v",
                f"{certificates}:/certificates:ro",
                "neo4j:2026.08.1-enterprise",
            ]
        )

    context = ssl.create_default_context(cafile=str(ROOT / "ca.pem"))
    deadline = time.monotonic() + 120
    while True:
        try:
            with (
                socket.create_connection(("127.0.0.1", 7687), timeout=5) as connection,
                context.wrap_socket(
                    connection, server_hostname="neo4j.worldcup.internal"
                ),
            ):
                break
        except OSError:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    "Private verified Bolt listener did not become ready"
                ) from None
            time.sleep(3)

    environment = json.loads((ROOT / "bootstrap/models.json").read_text()) | {
        "ENVIRONMENT": "demo",
        "RUNTIME": "local",
        "NEO4J_URI": "bolt://neo4j.worldcup.internal:7687",
        "NEO4J_ENCRYPTED": "true",
        "NEO4J_CA_CERTIFICATE": "/ca.pem",
        "NEO4J_BOOTSTRAP_PASSWORD": material["bootstrap_password"],
        "NEO4J_READER_PASSWORD": material["reader_password"],
        "NEO4J_WRITER_PASSWORD": material["writer_password"],
        "LANGSMITH_TRACING": "false",
        "PYTHONPATH": "/app/src:/var/task",
    }
    bootstrap = ROOT / "bootstrap.env"
    write_private_file(
        bootstrap,
        "\n".join(f"{name}={value}" for name, value in environment.items()) + "\n",
    )
    run_command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "--memory",
            "700m",
            "--add-host",
            "neo4j.worldcup.internal:127.0.0.1",
            "--env-file",
            str(bootstrap),
            "-v",
            f"{ROOT / 'bootstrap'}:/app",
            "-v",
            f"{ROOT / 'dependencies'}:/var/task:ro",
            "-v",
            f"{ROOT / 'ca.pem'}:/ca.pem:ro",
            "--entrypoint",
            "python",
            image,
            "-m",
            "world_cup_graph_agent.infrastructure.neo4j.bootstrap",
        ]
    )
    print("Database bootstrap completed with persistent verified Bolt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Configure the owned private database host"
    )
    parser.add_argument("volume_id")
    configure_database(parser.parse_args().volume_id)
