from hashlib import sha256
from json import dumps
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)
from typing import Annotated, Any, Literal, cast
from uuid import uuid4


_UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"

type ThreadId = Annotated[str, StringConstraints(pattern=_UUID_PATTERN)]

type RequestId = Annotated[str, StringConstraints(pattern=_UUID_PATTERN)]

type ApprovalId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class HostedPrincipal(Contract):
    # Only authentication adapters construct this identity. Email and display
    # names cannot establish durable ownership or select a user's secret.
    issuer: str = Field(min_length=1, max_length=2048, pattern=r"^https://")
    subject: str = Field(min_length=1, max_length=512)
    tenant: str | None = Field(default=None, min_length=1, max_length=512)

    def get_identity(self) -> str:
        # Structured encoding prevents ambiguous identities when claims contain
        # delimiters; the deployment owns the namespace around this digest.
        claims = dumps([self.issuer, self.subject, self.tenant], ensure_ascii=True)
        return sha256(claims.encode()).hexdigest()


class ConversationCreation(Contract):
    operation: Literal["create_conversation"]


class ConversationListing(Contract):
    operation: Literal["list_conversations"]
    limit: int = Field(default=20, ge=1, le=100)
    cursor: ThreadId | None = None


class ConversationReading(Contract):
    operation: Literal["read_conversation"]
    thread_id: ThreadId
    limit: int = Field(default=50, ge=1, le=100)


class MessageSubmission(Contract):
    operation: Literal["send_message"]
    thread_id: ThreadId
    message: str = Field(min_length=1, max_length=16000, pattern=r"\S")


class ApprovalDecision(Contract):
    type: Literal["approve", "reject"]


class ApprovalResponse(Contract):
    approval_id: ApprovalId
    decisions: list[ApprovalDecision] = Field(min_length=1, max_length=100)


class ConversationResumption(Contract):
    operation: Literal["resume_conversation"]
    thread_id: ThreadId
    approvals: list[ApprovalResponse] = Field(min_length=1, max_length=100)


type ConversationRequest = Annotated[
    ConversationCreation
    | ConversationListing
    | ConversationReading
    | MessageSubmission
    | ConversationResumption,
    Field(discriminator="operation"),
]

request_adapter = TypeAdapter(ConversationRequest)


class HostedInvocation(Contract):
    # GCP trusts this assertion only behind gateway-scoped invocation IAM.
    # Public requests are validated as ConversationRequest, never this envelope.
    principal: HostedPrincipal
    request: ConversationRequest
    traceparent: str | None = Field(
        default=None,
        pattern=r"^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$",
    )


class ApprovalAction(Contract):
    name: str
    arguments: dict[str, JsonValue]
    description: str | None = None


class PendingApproval(Contract):
    approval_id: ApprovalId
    interrupt_id: str
    actions: list[ApprovalAction]


class ConversationMessage(Contract):
    role: Literal["user", "assistant"]
    content: str


class ConversationSummary(Contract):
    thread_id: ThreadId
    status: Literal["idle", "active", "uncertain", "approval_required"]


class ResponseEnvelope(Contract):
    request_id: RequestId
    thread_id: ThreadId | None = None
    status: Literal["idle", "active", "uncertain", "approval_required", "error"]
    messages: list[ConversationMessage] = Field(default_factory=list)
    conversations: list[ConversationSummary] = Field(default_factory=list)
    pending_approvals: list[PendingApproval] = Field(default_factory=list)
    cursor: ThreadId | None = None
    error_code: (
        Literal[
            "invalid_request",
            "unauthenticated",
            "conversation_not_found",
            "execution_conflict",
            "execution_uncertain",
            "approval_required",
            "invalid_approval",
            "credential_unavailable",
            "runtime_unavailable",
        ]
        | None
    ) = None


def create_error_response(
    code: str, request_id: str | None = None, thread_id: str | None = None
) -> ResponseEnvelope:
    try:
        return ResponseEnvelope(
            request_id=request_id or str(uuid4()),
            thread_id=thread_id,
            status="error",
            error_code=cast(Any, code),
        )
    except ValidationError:
        return ResponseEnvelope(
            request_id=str(uuid4()), status="error", error_code="runtime_unavailable"
        )
