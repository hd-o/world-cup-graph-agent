import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from scripts.shared.configuration import ROOT
from typing import Protocol


TIMESTAMP_PATTERN = re.compile(r"\d{8}T\d{12}Z")
CURRENT_INVOCATION: ContextVar[Invocation | None] = ContextVar(
    "current_invocation", default=None
)
CURRENT_BUILD_FOLDER: ContextVar[Path | None] = ContextVar(
    "current_build_folder", default=None
)


class DeploymentTargets(Protocol):
    @property
    def deployment(self) -> str: ...


@dataclass(frozen=True)
class Invocation:
    command: str
    runtime: str
    timestamp: str
    folder: Path
    source_timestamp: str | None = None
    preview: bool = False


def create_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def validate_timestamp(timestamp: str) -> str:
    if not TIMESTAMP_PATTERN.fullmatch(timestamp):
        raise ValueError("Timestamp must use UTC microseconds: YYYYMMDDTHHMMSSffffffZ")
    return timestamp


def get_invocation() -> Invocation:
    invocation = CURRENT_INVOCATION.get()
    if invocation is None:
        raise RuntimeError("Generated files require an active command invocation")
    return invocation


def get_generated_path(*parts: str) -> Path:
    return get_invocation().folder.joinpath(*parts)


def get_build_path(filename: str) -> Path:
    folder = CURRENT_BUILD_FOLDER.get()
    if folder is None:
        raise RuntimeError("This operation requires a selected build invocation")
    return folder / filename


def bind_build(folder: Path) -> None:
    CURRENT_BUILD_FOLDER.set(folder)


def read_metadata(folder: Path) -> dict:
    return json.loads((folder / "metadata.json").read_text())


def load_invocation_outputs(
    command: str, runtime: str, timestamp: str, name: str
) -> dict:
    validate_timestamp(timestamp)
    folder = ROOT / "deploy/artifacts" / command / f"{runtime}-{timestamp}"
    metadata = read_metadata(folder)
    if (
        metadata.get("command") != command
        or metadata.get("runtime") != runtime
        or metadata.get("timestamp") != timestamp
        or metadata.get("status") != "succeeded"
    ):
        raise ValueError(f"Invocation {command}/{runtime}-{timestamp} did not succeed")
    return json.loads((folder / "outputs" / f"{name}.json").read_text())


def write_metadata(invocation: Invocation, fields: dict) -> None:
    path = invocation.folder / "metadata.json"
    metadata = json.loads(path.read_text()) | fields
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600), "w") as stream:
        stream.write(json.dumps(metadata, indent=2) + "\n")


def record_target(targets: DeploymentTargets, destination: str, region: str) -> None:
    invocation = get_invocation()
    write_metadata(
        invocation,
        {
            "deployment": targets.deployment,
            "destination": destination,
            "region": region,
        },
    )


def require_build(runtime: str, timestamp: str) -> Path:
    validate_timestamp(timestamp)
    folder = ROOT / "deploy/artifacts/build" / f"{runtime}-{timestamp}"
    if not folder.is_dir():
        raise FileNotFoundError(f"Build {runtime}-{timestamp} does not exist")
    metadata = read_metadata(folder)
    if (
        metadata.get("command") != "build"
        or metadata.get("runtime") != runtime
        or metadata.get("timestamp") != timestamp
        or metadata.get("status") != "succeeded"
    ):
        raise ValueError(f"Build {runtime}-{timestamp} did not complete successfully")
    return folder


def require_deployment(runtime: str, timestamp: str) -> Path:
    validate_timestamp(timestamp)
    folder = ROOT / "deploy/artifacts/deploy" / f"{runtime}-{timestamp}"
    if not folder.is_dir():
        raise FileNotFoundError(f"Deployment {runtime}-{timestamp} does not exist")
    metadata = read_metadata(folder)
    if (
        metadata.get("command") != "deploy"
        or metadata.get("runtime") != runtime
        or metadata.get("timestamp") != timestamp
        or metadata.get("status") != "succeeded"
        or metadata.get("preview")
    ):
        raise ValueError(
            f"Deployment {runtime}-{timestamp} is not a successful applied deployment"
        )
    invocation = CURRENT_INVOCATION.get()
    if invocation is not None:
        write_metadata(
            invocation,
            {
                "deployment": metadata.get("deployment"),
                "destination": metadata.get("destination"),
                "region": metadata.get("region"),
            },
        )
    return folder


@contextmanager
def select_build(folder: Path) -> Iterator[None]:
    token = CURRENT_BUILD_FOLDER.set(folder)
    try:
        yield
    finally:
        CURRENT_BUILD_FOLDER.reset(token)


@contextmanager
def open_invocation(
    command: str,
    runtime: str,
    timestamp: str | None = None,
    source_timestamp: str | None = None,
    preview: bool = False,
) -> Iterator[Invocation]:
    selected_timestamp = (
        validate_timestamp(timestamp) if timestamp else create_timestamp()
    )
    if source_timestamp:
        validate_timestamp(source_timestamp)
    folder = ROOT / "deploy/artifacts" / command / f"{runtime}-{selected_timestamp}"
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    invocation = Invocation(
        command,
        runtime,
        selected_timestamp,
        folder,
        source_timestamp,
        preview,
    )
    metadata = {
        "command": command,
        "runtime": runtime,
        "timestamp": selected_timestamp,
        "source_timestamp": source_timestamp,
        "preview": preview,
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
    }
    (folder / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (folder / "metadata.json").chmod(0o600)
    token = CURRENT_INVOCATION.set(invocation)
    build_token = CURRENT_BUILD_FOLDER.set(None)
    try:
        yield invocation
    except BaseException as error:
        write_metadata(
            invocation,
            {
                "status": "failed",
                "failure": type(error).__name__,
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )
        raise
    else:
        write_metadata(
            invocation,
            {"status": "succeeded", "completed_at": datetime.now(UTC).isoformat()},
        )
    finally:
        CURRENT_BUILD_FOLDER.reset(build_token)
        CURRENT_INVOCATION.reset(token)


def run_nested_invocation[Result](
    command: str,
    runtime: str,
    timestamp: str,
    operation: Callable[[], Result],
) -> Result:
    with open_invocation(command, runtime, timestamp):
        return operation()
