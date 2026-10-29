import asyncio
import pytest
from aiomisc import timeout
from collections.abc import Iterator
from contextlib import contextmanager
from evals.database import reset_eval_database
from evals.settings import EvalSettings
from pydantic import SecretStr
from scripts.shared.approvals import create_approval_request
from starlette.testclient import TestClient
from unittest.mock import patch
from world_cup_graph_agent.infrastructure.neo4j.driver import GraphDrivers
from world_cup_graph_agent.runtimes import agentcore, google
from world_cup_graph_agent.runtimes.contracts import HostedPrincipal


pytestmark = [pytest.mark.adapters]


@contextmanager
def open_google_client(settings: EvalSettings) -> Iterator[TestClient]:
    class CredentialsFixture:
        async def get_openrouter_api_key(self, principal: HostedPrincipal) -> SecretStr:
            return settings.openrouter_api_key

    # Only cloud configuration and secret sources are fixtures. The HTTP app,
    # startup probes, serving drivers and shutdown use the real local database.
    with (
        patch.object(google, "load_hosted_settings", return_value=settings),
        patch.object(google, "UserSecrets", return_value=CredentialsFixture()),
        TestClient(google.app) as client,
    ):
        yield client


@pytest.mark.usefixtures("drivers")
def test_google_http_lifecycle(settings: EvalSettings) -> None:
    configuration = settings.model_copy(
        update={"google_cloud_project": "adapter-evaluation", "identity_tenant": "demo"}
    )
    principal = HostedPrincipal(
        issuer="https://securetoken.google.com/adapter-evaluation",
        subject="http-lifecycle",
        tenant="demo",
    ).model_dump(mode="json")
    with open_google_client(configuration) as client:
        assert client.get("/health").json() == {"status": "ready"}
        invocation = {
            "principal": principal,
            "request": {"operation": "list_conversations"},
        }
        response = client.post("/invoke", json=invocation)
        assert response.status_code == 200
        assert response.json()["status"] == "idle", response.json().get("error_code")
        for request in (
            {"operation": "list_conversations", "limit": 20.0},
            {"operation": "list_conversations", "limit": "20"},
            {"operation": "list_conversations", "limit": True},
            {"operation": "list_conversations", "extra": "forbidden"},
        ):
            response = client.post("/invoke", json=invocation | {"request": request})
            assert response.json()["error_code"] == "invalid_request"
        for claims in (
            principal | {"tenant": "another-tenant"},
            principal | {"issuer": "https://securetoken.google.com/another-project"},
        ):
            response = client.post("/invoke", json=invocation | {"principal": claims})
            assert response.json()["error_code"] == "unauthenticated"
        for body in (b"{", b" " * 100001):
            response = client.post("/invoke", content=body)
            assert response.json()["error_code"] == "invalid_request"
        response = client.post(
            "/invoke", json=invocation | {"baggage": "private=secret"}
        )
        assert response.json()["error_code"] == "invalid_request"

    assert google.app.state.hosted is None


@pytest.mark.usefixtures("drivers")
def test_agentcore_worker_lifecycle(settings: EvalSettings) -> None:
    """Exercise SDK lifespan and worker dispatch against the real database."""
    principal = HostedPrincipal(
        issuer="https://cognito-idp.us-east-1.amazonaws.com/adapter-evaluation",
        subject="worker-lifecycle",
    )

    class AuthenticationFixture:
        async def verify_token(self, authorization: str) -> HostedPrincipal:
            return principal

    class CredentialsFixture:
        async def get_openrouter_api_key(self, principal: HostedPrincipal) -> SecretStr:
            raise AssertionError("Metadata operations must not resolve a model key")

    # Auth/secret/export sources are local fixtures. SDK worker dispatch,
    # startup readiness, serving connections and shutdown all stay real.
    with (
        patch.object(agentcore, "load_hosted_settings", return_value=settings),
        patch.object(
            agentcore, "CognitoVerifier", return_value=AuthenticationFixture()
        ),
        patch.object(agentcore, "UserSecrets", return_value=CredentialsFixture()),
        patch(
            "world_cup_graph_agent.observability.telemetry.configure_cloud_telemetry"
        ),
        TestClient(agentcore.app) as client,
    ):
        creation = client.post(
            "/invocations", json={"operation": "create_conversation"}
        )
        assert creation.status_code == 200
        response = creation.json()
        assert response["status"] == "idle", response.get("error_code")
        reading = client.post(
            "/invocations",
            json={
                "operation": "read_conversation",
                "thread_id": response["thread_id"],
            },
        )
        assert reading.status_code == 200
        assert reading.json()["status"] == "idle", reading.json().get("error_code")
        assert reading.json()["messages"] == []
        rejected = client.post(
            "/invocations",
            json={
                "operation": "list_conversations",
                "_agent_core_app_action": "job_status",
            },
        )
        assert rejected.status_code == 200
        assert rejected.json()["error_code"] == "invalid_request"


@timeout(600)
async def test_google_http_approval_persistence(
    settings: EvalSettings,
    drivers: GraphDrivers,
) -> None:
    """Restore and reject a persisted approval through a fresh adapter."""
    await reset_eval_database(settings, drivers.writer)
    configuration = settings.model_copy(
        update={
            "google_cloud_project": "adapter-evaluation",
            "identity_tenant": "demo",
        }
    )
    alice = HostedPrincipal(
        issuer="https://securetoken.google.com/adapter-evaluation",
        subject="alice",
        tenant="demo",
    )
    bob = alice.model_copy(update={"subject": "bob"})

    async def query(request: dict, principal: HostedPrincipal = alice) -> dict:
        response = await asyncio.to_thread(
            client.post,
            "/invoke",
            json={
                "principal": principal.model_dump(),
                "request": request,
            },
        )
        assert response.status_code == 200
        return response.json()

    with open_google_client(configuration) as client:
        response = await query({"operation": "create_conversation"})
        thread_id = response["thread_id"]
        assert response["status"] == "idle"
        response = await query(
            {
                "operation": "send_message",
                "thread_id": thread_id,
                "message": "What is Kylian Mbappe's birthplace?",
            }
        )
        for confirmation in (
            "Yes, please research it",
            "Yes, I agree with this plan, please go ahead",
        ):
            if response["pending_approvals"]:
                break
            response = await query(
                {
                    "operation": "send_message",
                    "thread_id": thread_id,
                    "message": confirmation,
                }
            )

        assert response["status"] == "approval_required", response.get("error_code")
        approvals = response["pending_approvals"]
        assert {
            action["name"] for approval in approvals for action in approval["actions"]
        } == {"research_web"}
        reading = {"operation": "read_conversation", "thread_id": thread_id}
        assert (await query(reading))["pending_approvals"] == approvals
        history = (await query(reading))["messages"]
        assert history and history[0]["role"] == "user"
        assert (await query({"operation": "list_conversations"}, bob))[
            "conversations"
        ] == []
        assert (await query(reading, bob))["error_code"] == "conversation_not_found"

    # A fresh application lifespan and drivers must recover the checkpoint
    # rather than relying on process-local state or open serving connections.
    with open_google_client(configuration) as client:
        restored = await query(reading)
        assert restored["pending_approvals"] == approvals
        assert restored["messages"] == history

        rejection = create_approval_request(restored, "reject")
        resumed = await query(rejection)
        assert resumed["status"] == "idle", resumed.get("error_code")
        assert not resumed["pending_approvals"]
        assert (await query(rejection))["error_code"] == "invalid_approval"
