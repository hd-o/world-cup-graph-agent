import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from scripts.shared.configuration import ROOT
from tempfile import TemporaryDirectory


RESOURCES = (
    "skills/querying/world-cup-querying/SKILL.md",
    "skills/querying/world-cup-querying/schema.json",
    "skills/enrichment/world-cup-enrichment/SKILL.md",
)


def export_requirements(group: str) -> Path:
    from scripts.shared.invocations import get_generated_path

    requirements = get_generated_path(f"requirements-{group}.txt")
    subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--no-default-groups",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--group",
            group,
            "--output-file",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        timeout=300,
    )
    return requirements


def copy_application(folder: Path) -> None:
    shutil.copytree(
        ROOT / "src/world_cup_graph_agent",
        folder / "world_cup_graph_agent",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for resource in RESOURCES:
        packaged = folder / "world_cup_graph_agent" / resource
        source = ROOT / "src/world_cup_graph_agent" / resource
        if not packaged.is_file() or packaged.read_bytes() != source.read_bytes():
            raise ValueError(f"Missing or mismatched package resource: {resource}")


def build_artifact(
    name: str, suffix: str, builder: Callable[[Path, Path], dict]
) -> dict:
    from scripts.shared.invocations import get_invocation

    artifacts = get_invocation().folder
    output = artifacts / f"{name}{suffix}"
    with TemporaryDirectory(prefix=f"{name}-build-", dir="/tmp/opencode") as temporary:
        evidence = builder(Path(temporary), output)

    with output.open("rb") as artifact:
        checksum = hashlib.file_digest(artifact, "sha256").hexdigest()

    evidence = evidence | {
        "cloud": name,
        "artifact": output.name,
        "compressed_bytes": output.stat().st_size,
        "sha256": checksum,
    }
    (artifacts / f"{name}-manifest.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    return evidence
