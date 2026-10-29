import hashlib
import json
from scripts.gcp.configuration import Targets
from scripts.gcp.deployment import (
    initialize_backend,
    list_state_resources,
    load_state,
    prepare_application_workspace,
    validate_state_ownership,
)
from scripts.shared.invocations import get_build_path


def inspect_artifacts() -> list[dict]:
    evidence = []
    for cloud in ("gcp", "gateway"):
        manifest = json.loads(get_build_path(f"{cloud}-manifest.json").read_text())
        with get_build_path(f"{cloud}.tar.gz").open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if manifest["sha256"] != checksum:
            raise PermissionError("Artifact checksum does not match its build manifest")
        evidence.append(manifest)
    return evidence


def inspect_deployment(targets: Targets) -> dict:
    from scripts.gcp.bootstrap import verify_bootstrap

    folder = prepare_application_workspace("gcp-inspection")
    initialize_backend(targets, folder)
    state = load_state(targets, folder)
    validate_state_ownership(targets, state)
    verify_bootstrap(targets)
    return {
        "project": targets.gcp_project,
        "region": targets.gcp_region,
        "deployment": targets.deployment,
        "lineage": state["lineage"],
        "serial": state["serial"],
        "retained_bootstrap": "live configuration verified",
        "resources": [
            {"address": resource["address"], "type": resource["type"]}
            for resource in list_state_resources(state)
        ],
        "connections": {
            name: output["value"]
            for name, output in state.get("outputs", {}).items()
            if not output.get("sensitive")
        },
    }
