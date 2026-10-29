import asyncio
from world_cup_graph_agent.runtimes.contracts import HostedPrincipal


class CognitoVerifier:
    def __init__(self, issuer: str, client_id: str) -> None:
        import boto3
        from botocore.config import Config
        from jwt import PyJWKClient

        if not issuer.startswith("https://cognito-idp.") or not client_id:
            raise ValueError("Fixed Cognito issuer and public client are required")
        self.issuer = issuer
        self.client_id = client_id
        self.keys = PyJWKClient(f"{issuer}/.well-known/jwks.json", timeout=10)
        self.client = boto3.client(
            "cognito-idp",
            region_name=issuer.split(".", 2)[1],
            config=Config(
                connect_timeout=5, read_timeout=10, retries={"max_attempts": 0}
            ),
        )

    async def verify_token(self, authorization: str) -> HostedPrincipal:
        return await asyncio.to_thread(self._verify_token, authorization)

    def _verify_token(self, authorization: str) -> HostedPrincipal:
        import jwt

        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise PermissionError("unauthenticated")

        try:
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=self.issuer,
                options={
                    "verify_aud": False,
                    "require": ["exp", "iat", "iss", "sub", "token_use", "client_id"],
                },
            )
        except jwt.PyJWTError as error:
            raise PermissionError("unauthenticated") from error

        # Cognito API access tokens identify their OAuth client with client_id,
        # not the aud field used by ID tokens. Signature verification comes first.
        if claims["client_id"] != self.client_id or claims["token_use"] != "access":
            raise PermissionError("unauthenticated")

        # Signature/expiry checks alone cannot detect Cognito global sign-out.
        # GetUser authenticates this already-verified access token, not the
        # runtime IAM identity; unavailable verification stays fail-closed.
        try:
            user = self.client.get_user(AccessToken=token)
        except self.client.exceptions.NotAuthorizedException as error:
            raise PermissionError("unauthenticated") from error
        subject = next(
            attribute["Value"]
            for attribute in user["UserAttributes"]
            if attribute["Name"] == "sub"
        )
        if subject != claims["sub"]:
            raise PermissionError("unauthenticated")

        return HostedPrincipal(issuer=self.issuer, subject=claims["sub"])


def verify_google_token(
    authorization: str, project: str, tenant: str
) -> HostedPrincipal:
    from firebase_admin import auth, get_app, initialize_app, tenant_mgt

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or not project or not tenant:
        raise PermissionError("unauthenticated")

    try:
        application = get_app()
    except ValueError:
        application = initialize_app(options={"projectId": project})

    try:
        claims = tenant_mgt.auth_for_tenant(tenant, app=application).verify_id_token(
            token,
            check_revoked=True,
        )
    except (
        auth.InvalidIdTokenError,
        auth.RevokedIdTokenError,
        auth.UserDisabledError,
        ValueError,
    ) as error:
        raise PermissionError("unauthenticated") from error

    if (
        claims.get("iss") != f"https://securetoken.google.com/{project}"
        or claims.get("aud") != project
        or claims.get("firebase", {}).get("tenant") != tenant
    ):
        raise PermissionError("unauthenticated")

    return HostedPrincipal(issuer=claims["iss"], subject=claims["sub"], tenant=tenant)
