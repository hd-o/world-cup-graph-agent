import hashlib
import json
import shlex
import subprocess
from pathlib import Path
from scripts.gcp.configuration import Targets
from scripts.gcp.deployment import load_deployment_outputs
from scripts.shared.configuration import ROOT
from scripts.shared.database import build_bootstrap_bundle, create_database_material
from tempfile import TemporaryDirectory


def provision_google_database(targets: Targets) -> None:
    from google.api_core.exceptions import NotFound
    from google.cloud import secretmanager, storage

    session = targets.create_google_session()
    outputs = load_deployment_outputs(targets)
    response = session.get(
        f"https://compute.googleapis.com/compute/v1/projects/{targets.gcp_project}/zones/{outputs['database_zone']}/instances/{outputs['database']}",
        timeout=30,
    )
    response.raise_for_status()
    if response.json().get("labels", {}).get("deployment") != targets.deployment:
        raise PermissionError("Google database lacks deployment ownership")
    client = secretmanager.SecretManagerServiceClient()
    prefix = f"projects/{targets.gcp_project}/secrets/{targets.deployment}-database"
    try:
        material = json.loads(
            client.access_secret_version(
                name=f"{prefix}-bootstrap/versions/latest"
            ).payload.data
        )
    except NotFound:
        material = create_database_material()
        client.add_secret_version(
            parent=f"{prefix}-bootstrap",
            payload=secretmanager.SecretPayload(data=json.dumps(material).encode()),
        )
    runtime = {
        name: material[name]
        for name in ("reader_password", "writer_password", "ca_certificate")
    }
    try:
        existing = json.loads(
            client.access_secret_version(
                name=f"{prefix}-runtime/versions/latest"
            ).payload.data
        )
    except NotFound:
        client.add_secret_version(
            parent=f"{prefix}-runtime",
            payload=secretmanager.SecretPayload(data=json.dumps(runtime).encode()),
        )
    else:
        if existing != runtime:
            raise PermissionError(
                "Database runtime material differs; credential recovery required"
            )
    bucket_name = f"{targets.gcp_project}-{targets.deployment}-bootstrap"
    storage_client = storage.Client(project=targets.gcp_project)
    bucket = storage_client.lookup_bucket(bucket_name)
    if bucket is None:
        bucket = storage_client.bucket(bucket_name)
        bucket.labels = {
            "deployment": targets.deployment,
            "purpose": "database-bootstrap",
        }
        bucket.iam_configuration.uniform_bucket_level_access_enabled = True
        bucket.iam_configuration.public_access_prevention = "enforced"
        bucket = storage_client.create_bucket(bucket, location=targets.gcp_region)
    elif bucket.labels != {
        "deployment": targets.deployment,
        "purpose": "database-bootstrap",
    }:
        raise PermissionError("Database bootstrap bucket lacks deployment ownership")
    with TemporaryDirectory(
        prefix="google-database-", dir="/tmp/opencode"
    ) as temporary:
        bundle = build_bootstrap_bundle(Path(temporary))
        with bundle.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        bundle_key = f"bootstrap/{checksum}.tar.gz"
        bucket.blob(bundle_key).upload_from_filename(str(bundle))
        from scripts.shared.artifacts import export_requirements

        requirements = export_requirements("gcp")
        requirements_key = "bootstrap/requirements-gcp.txt"
        bucket.blob(requirements_key).upload_from_filename(str(requirements))
    identity = (
        f"{targets.deployment}-database@{targets.gcp_project}.iam.gserviceaccount.com"
    )
    policy = bucket.get_iam_policy(requested_policy_version=3)
    binding = {
        "role": "roles/storage.objectViewer",
        "members": {f"serviceAccount:{identity}"},
    }
    if not any(
        entry["role"] == binding["role"]
        and binding["members"] <= set(entry["members"])
        and not entry.get("condition")
        for entry in policy.bindings
    ):
        policy.bindings.append(binding)
        bucket.set_iam_policy(policy)
    # The VM fetches secret bytes itself: SSH arguments carry resource identities,
    # never credentials, and umask protects the fetched material on the host.
    fetch = (
        """import base64, json, urllib.request, urllib.parse
from pathlib import Path
token = json.load(urllib.request.urlopen(urllib.request.Request(
    'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
    headers={'Metadata-Flavor': 'Google'})))['access_token']
resource = '{secret}'
endpoint = 'https://secretmanager.googleapis.com/v1/' + resource + ':access'
request = urllib.request.Request(endpoint,
    headers={'Authorization': 'Bearer ' + token})
payload = json.load(urllib.request.urlopen(request))['payload']['data']
Path('/opt/worldcup/bootstrap.json').write_bytes(base64.b64decode(payload))
downloads = {'{bundle}': '/opt/worldcup/bootstrap.tar.gz',
             '{requirements}': '/opt/worldcup/requirements.txt'}
for object_name, filename in downloads.items():
    endpoint = ('https://storage.googleapis.com/storage/v1/b/{bucket}/o/'
                + urllib.parse.quote(object_name, safe='') + '?alt=media')
    request = urllib.request.Request(endpoint,
        headers={'Authorization': 'Bearer ' + token})
    Path(filename).write_bytes(urllib.request.urlopen(request).read())
""".replace("{secret}", f"{prefix}-bootstrap/versions/latest")
        .replace("{bundle}", bundle_key)
        .replace("{requirements}", requirements_key)
        .replace("{bucket}", bucket_name)
    )
    commands = [
        "set -eu",
        "umask 077",
        "mkdir -p /opt/worldcup/bootstrap /opt/worldcup/dependencies",
        "apt-get update -qq",
        "apt-get -o DPkg::Lock::Timeout=300 install -y xfsprogs docker.io",
        "systemctl enable --now docker",
        "python3 -c " + shlex.quote(fetch),
        "tar -xzf /opt/worldcup/bootstrap.tar.gz -C /opt/worldcup/bootstrap",
        "docker run --rm -v /opt/worldcup:/installation python:3.14.4-slim "
        "pip install --no-compile --target /installation/dependencies "
        "-r /installation/requirements.txt",
        "python3 /opt/worldcup/bootstrap/configure.py google-worldcup-data",
    ]
    subprocess.run(
        [
            "gcloud",
            "compute",
            "ssh",
            outputs["database"],
            "--project",
            targets.gcp_project,
            "--zone",
            outputs["database_zone"],
            "--tunnel-through-iap",
            "--quiet",
            "--command",
            "sudo bash -c " + shlex.quote("\n".join(commands)),
        ],
        cwd=ROOT,
        env=targets.build_environment(),
        check=True,
        timeout=1200,
        capture_output=True,
    )
