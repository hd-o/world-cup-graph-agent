import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from scripts.shared.artifacts import build_artifact as package_artifact
from scripts.shared.artifacts import copy_application, export_requirements
from scripts.shared.configuration import ROOT


def build_google_artifact(folder: Path, requirements: Path, output: Path) -> dict:
    copy_application(folder)
    shutil.copyfile(requirements, folder / "requirements.txt")
    write_container_configuration(folder, "google")
    with tarfile.open(output, "w:gz") as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(folder)))
    return {}


def write_container_configuration(folder: Path, module: str) -> None:
    command = json.dumps(
        [
            "uvicorn",
            f"world_cup_graph_agent.runtimes.{module}:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8080",
            "--no-access-log",
        ]
    )
    (folder / "Dockerfile").write_text(f"""FROM python:3.14.4-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY world_cup_graph_agent ./world_cup_graph_agent
USER 65532:65532
CMD {command}
""")


def build_gateway_artifact(folder: Path, output: Path) -> dict:
    from scripts.shared.invocations import get_generated_path

    requirements = get_generated_path("requirements-gateway.txt")
    subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--only-group",
            "gateway",
            "--no-hashes",
            "--output-file",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    shutil.copyfile(requirements, folder / "requirements.txt")
    for module in (
        "runtimes/gateway.py",
        "runtimes/contracts.py",
        "runtimes/identity.py",
        "observability/telemetry.py",
    ):
        destination = folder / "world_cup_graph_agent" / module
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "src/world_cup_graph_agent" / module, destination)
    write_container_configuration(folder, "gateway")
    with tarfile.open(output, "w:gz") as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(folder)))
    return {}


def build_artifact(cloud: str = "gcp") -> dict:
    if cloud not in ("gcp", "gateway"):
        raise ValueError("GCP build supports gcp and gateway artifacts")
    if cloud == "gateway":
        return package_artifact("gateway", ".tar.gz", build_gateway_artifact)
    requirements = export_requirements("gcp")
    return package_artifact(
        "gcp",
        ".tar.gz",
        lambda folder, output: build_google_artifact(folder, requirements, output),
    )


def build_artifacts() -> list[dict]:
    return [build_artifact("gcp"), build_artifact("gateway")]
