import json
import os
import re
import secrets
from collections.abc import Callable
from pydantic import SecretStr
from scripts.shared.configuration import ROOT
from scripts.shared.conversations import HostedClient
from typing import Protocol


class EnrollmentTargets(Protocol):
    @property
    def deployment(self) -> str: ...

    @property
    def inputs(self) -> dict[str, SecretStr]: ...


def read_user_input(
    targets: EnrollmentTargets, username: str, purpose: str
) -> str | None:
    tokens = {
        "openrouter": ("OPENROUTER",),
        "password": ("PASSWORD",),
        "email": ("EMAIL",),
    }[purpose]
    values = [
        value.get_secret_value()
        for name, value in targets.inputs.items()
        if username.upper() in name.upper().split("_")
        and all(token in name.upper().split("_") for token in tokens)
    ]
    if len(values) > 1:
        raise ValueError(f"Ambiguous {username} {purpose} provisioning inputs")
    return values[0] if values else None


def load_login_password(targets: EnrollmentTargets, username: str) -> str:
    supplied = read_user_input(targets, username, "password")
    if supplied:
        return supplied
    if not re.fullmatch(r"[A-Za-z0-9_-]+", username):
        raise ValueError("Protected login identity must be a simple filename component")
    folder = ROOT / "deploy/private"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{username}-login.json"
    if path.exists():
        path.chmod(0o600)
        return json.loads(path.read_text())["password"]
    password = secrets.token_urlsafe(24) + "aA1!"
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as stream:
        json.dump({"password": password}, stream)
    return password


def read_demo_email(targets: EnrollmentTargets, username: str) -> str:
    return (
        read_user_input(targets, username, "email")
        or f"{username}@{targets.deployment}.example"
    )


def login_demo_user(
    targets: EnrollmentTargets,
    username: str,
    outputs: dict,
    login: Callable[[str, str, dict], HostedClient],
    login_name: str | None = None,
) -> HostedClient:
    return login(
        login_name or username, load_login_password(targets, username), outputs
    )


def enroll_users(
    targets: EnrollmentTargets,
    enrollment: Callable[[str, str, str | None], None],
    usernames: tuple[str, ...] = ("alice", "bob"),
) -> None:
    for username in usernames:
        key = read_user_input(targets, username, "openrouter")
        if not key:
            raise ValueError(
                f"{username} requires a protected per-user OpenRouter input"
            )
        password = load_login_password(targets, username)
        enrollment(username, password, key)
