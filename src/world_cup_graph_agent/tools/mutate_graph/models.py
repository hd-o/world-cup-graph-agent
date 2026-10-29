from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Literal
from world_cup_graph_agent.graph.schema.relationships import RelationshipType
from world_cup_graph_agent.tools.research_web import ResearchFinding


type MutationOperationKind = Literal[
    "create_node",
    "fill_node_properties",
    "create_relationship",
    "fill_relationship_properties",
]

type CreatableNodeTypeName = Literal[
    "Edition",
    "Competition",
    "Group",
    "Team",
    "Player",
    "Club",
    "Match",
    "Venue",
    "Goal",
]


class MutationOutcomeState(StrEnum):
    CREATED = "created"
    ALREADY_PRESENT = "already_present"


class MutationOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    op: MutationOperationKind
    node_type: CreatableNodeTypeName | None = None
    # Strict mode would reject plain wire strings for the StrEnum, so this
    # field alone coerces while the model stays strict everywhere else.
    relationship_type: RelationshipType | None = Field(default=None, strict=False)
    id: str | None = None
    start_id: str | None = None
    end_id: str | None = None
    properties: dict[str, Any] | None = None
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[ResearchFinding] = Field(min_length=1)


class MutateGraphArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operations: list[MutationOperation] = Field(min_length=1)
    risk_description: str = Field(min_length=1, max_length=2000)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        # OpenAI-compatible endpoints reject $defs, $ref, oneOf, and
        # discriminator blocks, so the published schema inlines nested
        # model definitions while validation keeps the nested models.
        schema = super().model_json_schema(*args, **kwargs)
        definitions = schema.pop("$defs", {})

        return _inline_schema_refs(schema, definitions)


class MutationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    index: int = Field(ge=0)
    operation: MutationOperationKind
    result: MutationOutcomeState
    target_id: str | None = None


class MutateGraphSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["applied"]
    outcomes: list[MutationOutcome]


class MutateGraphFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["failed"]
    failed_index: int = Field(ge=0)
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    rolled_back: bool


class MutationBatchError(Exception):
    def __init__(self, failed_index: int, code: str, message: str) -> None:
        self.failed_index = failed_index
        self.code = code
        self.message = message
        super().__init__(message)


def _inline_schema_refs(node: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if set(node.keys()) == {"$ref"}:
            reference = str(node["$ref"])
            definition_name = reference.split("/")[-1]
            definition = definitions.get(definition_name, {})

            return _inline_schema_refs(definition, definitions)

        return {
            key: _inline_schema_refs(value, definitions) for key, value in node.items()
        }

    if isinstance(node, list):
        return [_inline_schema_refs(item, definitions) for item in node]

    return node
