import re
from scripts.gcp.configuration import Targets
from scripts.gcp.deployment import list_state_resources


def read_state_settings(state: dict) -> dict:
    settings: dict = {}
    for resource in list_state_resources(state):
        values = resource["values"]
        if resource["address"] in (
            "google_cloud_run_v2_service.gateway",
            "google_cloud_run_v2_service.agent",
        ):
            container = values["template"][0]["containers"][0]
            service = resource["address"].rsplit(".", 1)[-1]
            settings[f"{service}_image"] = container["image"]
            if service == "agent":
                settings["model_environment"] = {
                    entry["name"]: entry["value"]
                    for entry in container.get("env", [])
                    if re.fullmatch(
                        r"OPENROUTER_(SUPERVISOR|ENRICHER|RESEARCH)_"
                        r"(MODEL_ID|PROVIDERS|SORT|REASONING_EFFORT)",
                        entry["name"],
                    )
                }
        elif resource["address"] == "google_compute_instance.database":
            settings["database_machine"] = values["machine_type"].split("/")[-1]
    return settings


def confirm_handover_target(
    targets: Targets, target: str | None, deployment: str | None
) -> None:
    if (target, deployment) != (targets.gcp_project, targets.deployment):
        raise PermissionError("Exact project and deployment confirmations are required")
