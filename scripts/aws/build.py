import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from scripts.shared.artifacts import build_artifact as build_shared_artifact
from scripts.shared.artifacts import copy_application, export_requirements
from scripts.shared.configuration import ROOT


def verify_native_libraries(folder: Path) -> dict:
    libraries = list(folder.rglob("*.so")) + list(folder.rglob("*.so.*"))
    versions = set()
    for library in libraries:
        header = subprocess.run(
            ["readelf", "-h", str(library)], capture_output=True, text=True, check=True
        ).stdout
        if "AArch64" not in header:
            raise ValueError(f"Non-ARM64 native library: {library.name}")
        symbols = subprocess.run(
            ["readelf", "--version-info", str(library)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for version in re.findall(r"\bGLIBC_(\d+\.\d+)\b", symbols):
            versions.add(tuple(map(int, version.split("."))))
    highest = max(versions, default=(0, 0))
    if highest > (2, 34):
        raise ValueError("Native dependencies exceed Amazon Linux 2023 GLIBC 2.34")
    return {
        "native_libraries": len(libraries),
        "highest_glibc": ".".join(map(str, highest)),
    }


def build_aws_artifact(folder: Path, requirements: Path, output: Path) -> dict:
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            "aarch64-manylinux_2_34",
            "--python-version",
            "3.14",
            "--target",
            str(folder),
            "--no-compile-bytecode",
            "--only-binary",
            ":all:",
            "-r",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    copy_application(folder)
    launcher = folder / "serve.py"
    launcher.write_text(
        "#!/usr/bin/env python3\n"
        "from world_cup_graph_agent.observability import telemetry\n"
        "telemetry.configure_cloud_telemetry('aws')\n"
        "from world_cup_graph_agent.runtimes.agentcore import app\n"
        "app.run()\n"
    )
    launcher.chmod(0o755)
    for executable in (folder / "bin").iterdir():
        if executable.is_file():
            content = executable.read_bytes()
            if content.startswith(b"#!"):
                executable.write_bytes(
                    b"#!/usr/bin/env python3\n" + content.split(b"\n", 1)[1]
                )
            executable.chmod(0o755)
    instrument = folder / "opentelemetry-instrument"
    shutil.copyfile(folder / "bin/opentelemetry-instrument", instrument)
    instrument.chmod(0o755)
    native = verify_native_libraries(folder)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for filename in sorted(folder.rglob("*")):
            if filename.is_file() and "__pycache__" not in filename.parts:
                filename.chmod(
                    0o755
                    if filename in (launcher, instrument)
                    or filename.parent == folder / "bin"
                    else 0o644
                )
                archive.write(filename, filename.relative_to(folder))
    with zipfile.ZipFile(output) as archive:
        expanded = sum(member.file_size for member in archive.infolist())
    if output.stat().st_size > 250_000_000 or expanded > 750_000_000:
        raise ValueError("AgentCore ZIP exceeds direct-code size limits")
    return native | {"expanded_bytes": expanded}


def build_artifact() -> None:
    from scripts.shared.interface import show_inventory

    requirements = export_requirements("aws")
    evidence = build_shared_artifact(
        "aws",
        ".zip",
        lambda folder, output: build_aws_artifact(folder, requirements, output),
    )
    show_inventory("AWS direct ARM64/Python 3.14 artifact", evidence)
