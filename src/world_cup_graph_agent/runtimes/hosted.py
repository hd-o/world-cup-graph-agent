import asyncio
import langsmith
from collections.abc import Awaitable, Callable
from hashlib import sha256
from json import dumps
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, StateSnapshot
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import SecretStr
from typing import Any, cast
from uuid import uuid4
from world_cup_graph_agent.agent.factory import create_app_agent
from world_cup_graph_agent.config.settings import Settings
from world_cup_graph_agent.infrastructure.neo4j.checkpoints import (
    CheckpointConflict,
    CheckpointSaver,
)
from world_cup_graph_agent.infrastructure.neo4j.conversations import (
    ConversationError,
    ConversationStore,
)
from world_cup_graph_agent.infrastructure.neo4j.driver import GraphDrivers
from world_cup_graph_agent.infrastructure.secrets.credentials import CredentialError
from world_cup_graph_agent.observability.telemetry import create_tracing_client
from world_cup_graph_agent.runtimes.contracts import (
    ApprovalAction,
    ConversationCreation,
    ConversationListing,
    ConversationMessage,
    ConversationReading,
    ConversationRequest,
    ConversationResumption,
    HostedPrincipal,
    MessageSubmission,
    PendingApproval,
    ResponseEnvelope,
    create_error_response,
)


type CredentialResolver = Callable[[HostedPrincipal], Awaitable[SecretStr]]


class HostedApplication:
    def __init__(
        self,
        settings: Settings,
        drivers: GraphDrivers,
        credentials: CredentialResolver,
    ) -> None:
        self.settings = settings
        self.drivers = drivers
        self.credentials = credentials
        self.tracing_client = create_tracing_client()
        self.checkpointer = CheckpointSaver(
            drivers.writer, settings.neo4j_checkpoint_database
        )
        self.conversations = ConversationStore(
            drivers.writer, settings.neo4j_checkpoint_database
        )

    async def verify_connectivity(self) -> None:
        await self.drivers.reader.verify_connectivity()
        await self.drivers.writer.verify_connectivity()

    async def invoke(
        self, principal: HostedPrincipal, request: ConversationRequest
    ) -> ResponseEnvelope:
        request_id = str(uuid4())
        thread_id = getattr(request, "thread_id", None)
        try:
            return await self._invoke(principal, request, request_id)
        except (ConversationError, CredentialError) as error:
            code = (
                error.code
                if isinstance(error, ConversationError)
                else "credential_unavailable"
            )
            return create_error_response(code, request_id, thread_id)
        except CheckpointConflict:
            return create_error_response("execution_uncertain", request_id, thread_id)
        except Exception:
            # Exception strings can contain queries, model payloads or credentials.
            # Transports report fixed errors; operators inspect execution metadata.
            return create_error_response("runtime_unavailable", request_id, thread_id)

    async def _invoke(
        self, principal: HostedPrincipal, request: ConversationRequest, request_id: str
    ) -> ResponseEnvelope:
        owner = principal.get_identity()
        if isinstance(request, ConversationCreation):
            thread_id = await self.conversations.create_conversation(owner)
            return ResponseEnvelope(
                request_id=request_id, thread_id=thread_id, status="idle"
            )

        if isinstance(request, ConversationListing):
            conversations = await self.conversations.list_conversations(
                owner, request.limit + 1, request.cursor
            )
            cursor = (
                conversations[request.limit - 1].thread_id
                if len(conversations) > request.limit
                else None
            )
            return ResponseEnvelope(
                request_id=request_id,
                status="idle",
                conversations=conversations[: request.limit],
                cursor=cursor,
            )

        status = await self.conversations.read_status(owner, request.thread_id)
        if isinstance(request, ConversationReading):
            return await self.read_conversation(
                request.thread_id, request_id, status, request.limit
            )

        await self.conversations.admit_execution(owner, request.thread_id, request_id)
        invocation_started = False
        completion_status = "idle"
        try:
            # Read/check approval under the durable admission fence, before
            # resolving a key. Replayed decisions cannot resume a changed snapshot.
            graph = create_app_agent(
                self.settings,
                self.checkpointer,
                self.drivers,
                SecretStr("inspection-only"),
            )
            config: RunnableConfig = {
                "configurable": {"thread_id": request.thread_id},
                "recursion_limit": 100,
            }
            snapshot = await graph.aget_state(config)
            approvals = render_approvals(request.thread_id, snapshot)
            completion_status = "approval_required" if approvals else "idle"
            if isinstance(request, MessageSubmission):
                if approvals:
                    raise ConversationError("approval_required")
                # Non-interrupt pending tasks indicate an unfinished execution,
                # not an invitation to replay side effects with a new message.
                if snapshot.next:
                    completion_status = "uncertain"
                    raise ConversationError("execution_uncertain")
                graph_input = {"messages": [HumanMessage(content=request.message)]}
            else:
                graph_input = build_resume_command(request, approvals)

            openrouter_api_key = await self.credentials(principal)
            graph = create_app_agent(
                self.settings, self.checkpointer, self.drivers, openrouter_api_key
            )
            invocation_started = True
            # Transport timeouts must not blindly retry this operation. A timeout
            # during graph/checkpoint work holds the thread for operator inspection.
            tracer = trace.get_tracer("worldcup.agent")
            with (
                tracer.start_as_current_span(
                    "worldcup.agent",
                    record_exception=False,
                    set_status_on_exception=False,
                    attributes={
                        "operation": request.operation,
                        "request_id": request_id,
                        "thread_id": request.thread_id,
                        "gen_ai.operation.name": "invoke_agent",
                        "gen_ai.agent.name": "worldcup-agent",
                    },
                ) as span,
                langsmith.tracing_context(client=self.tracing_client),
            ):
                try:
                    async with asyncio.timeout(540):
                        await graph.ainvoke(
                            graph_input, config=config, durability="sync"
                        )
                        snapshot = await graph.aget_state(config)
                except BaseException:
                    span.set_status(Status(StatusCode.ERROR))
                    span.set_attribute("error.type", "execution_uncertain")
                    raise

                span.set_attribute(
                    "status", "approval_required" if snapshot.interrupts else "idle"
                )

            approvals = render_approvals(request.thread_id, snapshot)
            completion_status = "approval_required" if approvals else "idle"
            response = render_conversation(
                request.thread_id, request_id, snapshot, completion_status, 50
            )
        except BaseException as error:
            if invocation_started or isinstance(error, CheckpointConflict):
                completion_status = "uncertain"
            raise
        finally:
            await asyncio.shield(
                self.conversations.finish_execution(
                    request.thread_id, request_id, cast(Any, completion_status)
                )
            )

        return response

    async def read_conversation(
        self, thread_id: str, request_id: str, status: str, limit: int
    ) -> ResponseEnvelope:
        # Graph topology/state APIs do not need a model credential or inference.
        graph = create_app_agent(
            self.settings, self.checkpointer, self.drivers, SecretStr("inspection-only")
        )
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        return render_conversation(thread_id, request_id, snapshot, status, limit)


def render_approvals(thread_id: str, snapshot: StateSnapshot) -> list[PendingApproval]:
    approvals = []
    for task in snapshot.tasks:
        for interruption in task.interrupts:
            value = interruption.value
            actions = [
                ApprovalAction(
                    name=action["name"],
                    arguments=action["args"],
                    description=action.get("description"),
                )
                for action in value["action_requests"]
            ]
            binding = dumps(
                [
                    thread_id,
                    snapshot.config.get("configurable", {}).get("checkpoint_id"),
                    interruption.id,
                    value,
                ],
                sort_keys=True,
            )
            approvals.append(
                PendingApproval(
                    approval_id=sha256(binding.encode()).hexdigest(),
                    interrupt_id=interruption.id,
                    actions=actions,
                )
            )

    return approvals


def build_resume_command(
    request: ConversationResumption, approvals: list[PendingApproval]
) -> Command:
    if not approvals or len(request.approvals) != len(approvals):
        raise ConversationError("invalid_approval")

    resumes = {}
    for response, approval in zip(request.approvals, approvals, strict=True):
        if response.approval_id != approval.approval_id or len(
            response.decisions
        ) != len(approval.actions):
            raise ConversationError("invalid_approval")
        if approval.interrupt_id in resumes:
            raise ConversationError("invalid_approval")
        resumes[approval.interrupt_id] = {
            "decisions": [
                {"type": decision.type}
                if decision.type == "approve"
                else {
                    "type": "reject",
                    "message": (
                        "User rejected this action. End enrichment and return your "
                        "GraphEnrichmentReport with the corresponding rejection "
                        "status directly. Do not delegate or call further tools."
                    ),
                }
                for decision in response.decisions
            ]
        }

    # DeepAgents' sequential delegated workflow uses the documented flat
    # decision payload. ID-addressed values handle multiple simultaneous tasks.
    return Command(
        resume=next(iter(resumes.values())) if len(resumes) == 1 else resumes
    )


def render_conversation(
    thread_id: str, request_id: str, snapshot: StateSnapshot, status: str, limit: int
) -> ResponseEnvelope:
    messages = []
    for message in snapshot.values.get("messages", []):
        if isinstance(message, (HumanMessage, AIMessage)):
            messages.append(
                ConversationMessage(
                    role="user" if isinstance(message, HumanMessage) else "assistant",
                    content=get_message_text(message)[:32000],
                )
            )

    return ResponseEnvelope(
        request_id=request_id,
        thread_id=thread_id,
        status=cast(Any, status),
        messages=messages[-limit:],
        pending_approvals=render_approvals(thread_id, snapshot),
    )


def get_message_text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "\n".join(
        block.get("text", "") for block in message.content if isinstance(block, dict)
    )
