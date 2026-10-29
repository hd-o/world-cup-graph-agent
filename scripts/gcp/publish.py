import json
import subprocess
import tarfile
from pathlib import Path
from scripts.gcp.bootstrap import wait_for_operation
from scripts.gcp.configuration import Targets
from scripts.gcp.inspect import inspect_artifacts
from scripts.shared.configuration import save_outputs
from scripts.shared.invocations import get_build_path
from tempfile import TemporaryDirectory


def inspect_publication(targets: Targets) -> dict:
    session = targets.create_google_session()
    inspect_artifacts()
    parent = f"projects/{targets.gcp_project}/locations/{targets.gcp_region}"
    repository = f"{parent}/repositories/{targets.deployment}"
    response = session.get(
        f"https://artifactregistry.googleapis.com/v1/{repository}", timeout=30
    )
    if response.status_code != 404:
        response.raise_for_status()
        if response.json().get("labels", {}).get("deployment") != targets.deployment:
            raise PermissionError("Artifact repository lacks deployment ownership")
    image_repository = (
        f"{targets.gcp_region}-docker.pkg.dev/"
        f"{targets.gcp_project}/{targets.deployment}"
    )
    images = {}
    for component, artifact in (("agent", "gcp"), ("gateway", "gateway")):
        manifest = json.loads(get_build_path(f"{artifact}-manifest.json").read_text())
        images[component] = {
            "source_sha256": manifest["sha256"],
            "image": f"{image_repository}/{component}:{manifest['sha256'][:16]}",
            "artifact": artifact,
        }
    return {
        "repository": repository,
        "create_repository": response.status_code == 404,
        "images": images,
        "platform": "linux/amd64",
    }


def publish_images(targets: Targets, inspection: dict | None = None) -> dict[str, str]:
    inspection = inspection or inspect_publication(targets)
    if inspect_publication(targets) != inspection:
        raise PermissionError("Publication scope changed; review it again")
    session = targets.create_google_session()
    repository = inspection["repository"]
    endpoint = "https://artifactregistry.googleapis.com/v1"
    if inspection["create_repository"]:
        parent = repository.rsplit("/repositories/", 1)[0]
        response = session.post(
            f"{endpoint}/{parent}/repositories",
            params={"repositoryId": targets.deployment},
            json={"format": "DOCKER", "labels": {"deployment": targets.deployment}},
            timeout=30,
        )
        response.raise_for_status()
        wait_for_operation(session, endpoint, response.json(), 180)
    images = {
        f"{component}_image": publish_image(targets, settings)
        for component, settings in inspection["images"].items()
    }
    save_outputs("gcp-artifacts", images | {"repository": repository})
    return images


def publish_image(targets: Targets, settings: dict) -> str:
    from google.auth.transport.requests import Request

    session = targets.create_google_session()
    image = settings["image"]
    with TemporaryDirectory(prefix="image-publish-", dir="/tmp/opencode") as temporary:
        folder = Path(temporary)
        source = folder / "source"
        source.mkdir()
        with tarfile.open(get_build_path(f"{settings['artifact']}.tar.gz")) as archive:
            archive.extractall(source, filter="data")
        authentication = folder / "authentication"
        authentication.mkdir(mode=0o700)
        credentials = session.credentials
        credentials.refresh(Request())
        subprocess.run(
            [
                "docker",
                "--config",
                str(authentication),
                "login",
                "-u",
                "oauth2accesstoken",
                "--password-stdin",
                f"{targets.gcp_region}-docker.pkg.dev",
            ],
            input=credentials.token,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        subprocess.run(
            [
                "docker",
                "--config",
                str(authentication),
                "build",
                "--platform",
                "linux/amd64",
                "-t",
                image,
                str(source),
            ],
            check=True,
            timeout=600,
            capture_output=True,
        )
        subprocess.run(
            ["docker", "--config", str(authentication), "push", image],
            check=True,
            timeout=600,
            capture_output=True,
        )
        response = subprocess.run(
            [
                "docker",
                "--config",
                str(authentication),
                "inspect",
                image,
                "--format",
                "{{json .RepoDigests}}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        repository = image.rsplit(":", 1)[0]
        digests = [
            digest
            for digest in json.loads(response.stdout)
            if digest.startswith(repository + "@sha256:")
        ]
        if len(digests) != 1:
            raise PermissionError(
                "Published image lacks an unambiguous repository digest"
            )
    return digests[0]
