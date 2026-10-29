import re
from scripts.aws.configuration import Targets
from scripts.aws.deployment import load_deployment
from scripts.shared.enrollment import load_login_password, read_user_input
from scripts.shared.interface import confirm_action, show_inventory


def enroll_aws_user(
    targets: Targets, username: str, password: str, key: str | None
) -> None:
    from world_cup_graph_agent.runtimes.contracts import HostedPrincipal

    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", username):
        raise ValueError("Username must be a lowercase identifier")
    session = targets.create_aws_session()
    outputs = load_deployment(targets)
    client = session.client("cognito-idp")
    try:
        user = client.admin_get_user(
            UserPoolId=outputs["UserPoolId"], Username=username
        )
    except client.exceptions.UserNotFoundException:
        user = client.admin_create_user(
            UserPoolId=outputs["UserPoolId"],
            Username=username,
            MessageAction="SUPPRESS",
        )["User"]
    if user.get("UserStatus") == "FORCE_CHANGE_PASSWORD":
        client.admin_set_user_password(
            UserPoolId=outputs["UserPoolId"],
            Username=username,
            Password=password,
            Permanent=True,
        )
    # Existing credentials are a user boundary: reruns do not reset passwords or keys.
    attributes = user.get("UserAttributes", user.get("Attributes", []))
    subject = next(
        attribute["Value"] for attribute in attributes if attribute["Name"] == "sub"
    )
    principal = HostedPrincipal(
        issuer=f"https://cognito-idp.{targets.aws_region}.amazonaws.com/{outputs['UserPoolId']}",
        subject=subject,
    )
    if key is None:
        return
    store = session.client("secretsmanager")
    name = f"{targets.deployment}/users/{principal.get_identity()}/openrouter"
    try:
        secret = store.describe_secret(SecretId=name)
    except store.exceptions.ResourceNotFoundException:
        store.create_secret(
            Name=name,
            SecretString=key,
            Tags=[{"Key": "Deployment", "Value": targets.deployment}],
        )
    else:
        if {tag["Key"]: tag["Value"] for tag in secret.get("Tags", [])}.get(
            "Deployment"
        ) != targets.deployment:
            raise PermissionError(
                "Existing per-user model secret lacks deployment ownership"
            )
        if secret.get("DeletedDate"):
            raise PermissionError("Existing per-user model secret is pending deletion")


def validate_usernames(usernames: list[str]) -> None:
    if any(
        not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", username) for username in usernames
    ):
        raise ValueError("Username must be a lowercase identifier")
    if len(usernames) != len(set(usernames)):
        raise ValueError("Requested usernames must be distinct")


def enroll_users(
    targets: Targets,
    usernames: list[str] | None = None,
    keyless: bool = False,
    execution: bool = False,
) -> None:
    usernames = usernames or (["keyless"] if keyless else ["alice", "bob"])
    validate_usernames(usernames)
    keys = {
        username: None if keyless else read_user_input(targets, username, "openrouter")
        for username in usernames
    }
    if not keyless and any(not key for key in keys.values()):
        raise ValueError(
            "Each requested user requires a protected per-user OpenRouter input"
        )
    show_inventory(
        "AWS enrollment",
        {
            "account": targets.aws_account,
            "region": targets.aws_region,
            "deployment": targets.deployment,
            "users": usernames,
            "policy": "Create missing users and keys; preserve existing credentials",
        },
    )
    confirm_action("Enroll requested AWS users?", execution=execution)
    for username in usernames:
        enroll_aws_user(
            targets, username, load_login_password(targets, username), keys[username]
        )
