from scripts.gcp.configuration import Targets
from scripts.gcp.deployment import load_deployment_outputs
from scripts.shared.enrollment import load_login_password, read_user_input


def read_demo_email(targets: Targets, username: str) -> str:
    return (
        read_user_input(targets, username, "email")
        or f"{username}@{targets.deployment}.example"
    )


def enroll_google_user(
    targets: Targets, username: str, password: str, key: str | None
) -> None:
    from firebase_admin import auth, credentials, delete_app, initialize_app, tenant_mgt
    from google.api_core.exceptions import NotFound
    from google.cloud import secretmanager
    from world_cup_graph_agent.runtimes.contracts import HostedPrincipal

    outputs = load_deployment_outputs(targets)
    application = initialize_app(
        credentials.ApplicationDefault(),
        {"projectId": targets.gcp_project},
        name=f"{targets.deployment}-{username}",
    )
    try:
        client = tenant_mgt.auth_for_tenant(outputs["tenant"], app=application)
        try:
            user = client.get_user(username)
        except auth.UserNotFoundError:
            user = client.create_user(
                uid=username,
                email=read_demo_email(targets, username),
                password=password,
            )
        # Existing accounts retain their passwords; reruns are not a reset path.
        principal = HostedPrincipal(
            issuer=f"https://securetoken.google.com/{targets.gcp_project}",
            subject=user.uid,
            tenant=outputs["tenant"],
        )
        if key is None:
            return
        store = secretmanager.SecretManagerServiceClient()
        parent = f"projects/{targets.gcp_project}"
        name = f"{targets.deployment}-users-{principal.get_identity()}"
        try:
            metadata = store.get_secret(name=f"{parent}/secrets/{name}")
        except NotFound:
            store.create_secret(
                parent=parent,
                secret_id=name,
                secret=secretmanager.Secret(
                    {
                        "replication": {"automatic": {}},
                        "labels": {"deployment": targets.deployment},
                    }
                ),
            )
        else:
            if metadata.labels.get("deployment") != targets.deployment:
                raise PermissionError("Model secret ownership does not match")
            try:
                store.access_secret_version(
                    name=f"{parent}/secrets/{name}/versions/latest"
                )
            except NotFound:
                pass
            else:
                return
        store.add_secret_version(
            parent=f"{parent}/secrets/{name}",
            payload=secretmanager.SecretPayload(data=key.encode()),
        )
    finally:
        delete_app(application)


def enroll_users(
    targets: Targets,
    usernames: tuple[str, ...] = ("alice", "bob"),
    keyless: bool = False,
) -> None:
    # Validate all protected inputs before creating the first account.
    keys = {
        username: None if keyless else read_user_input(targets, username, "openrouter")
        for username in usernames
    }
    if not keyless and any(not key for key in keys.values()):
        raise ValueError(
            "Each requested user requires a protected per-user OpenRouter input"
        )
    for username in usernames:
        enroll_google_user(
            targets, username, load_login_password(targets, username), keys[username]
        )
