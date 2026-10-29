import json
import os
from dotenv import dotenv_values
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_ENV = ROOT / "deploy/.env"


def save_outputs(cloud: str, outputs: dict) -> None:
    from scripts.shared.invocations import CURRENT_INVOCATION, get_generated_path

    folder = (
        get_generated_path("outputs")
        if CURRENT_INVOCATION.get() is not None
        else ROOT / "deploy/outputs"
    )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{cloud}.json"
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
    ) as stream:
        stream.write(json.dumps(outputs, indent=2) + "\n")
    path.chmod(0o600)


def load_outputs(cloud: str) -> dict:
    return json.loads((ROOT / "deploy/outputs" / f"{cloud}.json").read_text())


def load_model_environment() -> dict[str, str]:
    configuration = dotenv_values(ROOT / ".env")
    names = {
        f"OPENROUTER_{role}_{setting}"
        for role in ("SUPERVISOR", "ENRICHER", "RESEARCH")
        for setting in ("MODEL_ID", "REASONING_EFFORT", "PROVIDERS", "SORT")
    }
    return {
        name: value
        for name, value in configuration.items()
        if name in names and value is not None
    }
