from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pydantic import BaseModel, ConfigDict
from world_cup_graph_agent.graph.schema.model.identity import (
    IdentityText,
    require_identity_input,
)
from world_cup_graph_agent.graph.schema.nodes import NodeType


class RelationshipType(StrEnum):
    HAS_EDITION = "HAS_EDITION"
    HAS_COMPETITION = "HAS_COMPETITION"
    HAS_GROUP = "HAS_GROUP"
    IN_GROUP = "IN_GROUP"
    COMPETING_IN = "COMPETING_IN"
    PLAYS_FOR = "PLAYS_FOR"
    CONTRACTED_TO = "CONTRACTED_TO"
    TEAM1 = "TEAM1"
    TEAM2 = "TEAM2"
    PLAYED_AT = "PLAYED_AT"
    IN_COMPETITION = "IN_COMPETITION"
    OCCURRED_IN = "OCCURRED_IN"
    SCORED_BY = "SCORED_BY"
    CREDITED_TO = "CREDITED_TO"
    CONCEDED_BY = "CONCEDED_BY"


class PlaysForCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    competition_id: str
    position: str | None = None
    shirt_number: int | None = None


class PlaysForFill(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    position: str | None = None
    shirt_number: int | None = None


@dataclass(frozen=True)
class RelationshipEntry:
    relationship_type: RelationshipType
    start_label: NodeType
    end_label: NodeType
    create: type[BaseModel] | None = None
    fill: type[BaseModel] | None = None
    build_id: Callable[..., str] | None = None


def list_relationship_types() -> list[str]:
    return [entry.relationship_type.value for entry in RELATIONSHIPS.values()]


def list_relationship_property_keys() -> list[str]:
    # Only PLAYS_FOR carries properties. Endpoint ids travel on the operation
    # itself, so every create field is a persisted property, and fill fields
    # are a subset of create fields; `id` is handler-built.
    keys = {"id"}

    for entry in RELATIONSHIPS.values():
        if entry.create is not None:
            keys.update(entry.create.model_fields)

    return sorted(keys)


def list_relationship_id_types() -> list[str]:
    return [
        entry.relationship_type.value
        for entry in RELATIONSHIPS.values()
        if entry.build_id is not None
    ]


def _build_plays_for_id(
    player_id: str,
    team_id: str,
    competition_id: str,
) -> IdentityText:
    # The same endpoints may be related in several competitions. Including
    # the competition keeps those legitimate relationships distinct.
    return (
        f"{require_identity_input('Relationship', 'type', 'PLAYS_FOR')}:"
        f"{require_identity_input('Relationship', 'start_id', player_id)}:"
        f"{require_identity_input('Relationship', 'end_id', team_id)}:"
        f"{require_identity_input('Relationship', 'context_id', competition_id)}"
    )


RELATIONSHIPS: dict[RelationshipType, RelationshipEntry] = {
    RelationshipType.HAS_EDITION: RelationshipEntry(
        relationship_type=RelationshipType.HAS_EDITION,
        start_label=NodeType.Tournament,
        end_label=NodeType.Edition,
    ),
    RelationshipType.HAS_COMPETITION: RelationshipEntry(
        relationship_type=RelationshipType.HAS_COMPETITION,
        start_label=NodeType.Edition,
        end_label=NodeType.Competition,
    ),
    RelationshipType.HAS_GROUP: RelationshipEntry(
        relationship_type=RelationshipType.HAS_GROUP,
        start_label=NodeType.Competition,
        end_label=NodeType.Group,
    ),
    RelationshipType.IN_GROUP: RelationshipEntry(
        relationship_type=RelationshipType.IN_GROUP,
        start_label=NodeType.Team,
        end_label=NodeType.Group,
    ),
    RelationshipType.COMPETING_IN: RelationshipEntry(
        relationship_type=RelationshipType.COMPETING_IN,
        start_label=NodeType.Team,
        end_label=NodeType.Competition,
    ),
    RelationshipType.PLAYS_FOR: RelationshipEntry(
        relationship_type=RelationshipType.PLAYS_FOR,
        start_label=NodeType.Player,
        end_label=NodeType.Team,
        create=PlaysForCreation,
        fill=PlaysForFill,
        build_id=_build_plays_for_id,
    ),
    RelationshipType.CONTRACTED_TO: RelationshipEntry(
        relationship_type=RelationshipType.CONTRACTED_TO,
        start_label=NodeType.Player,
        end_label=NodeType.Club,
    ),
    RelationshipType.TEAM1: RelationshipEntry(
        relationship_type=RelationshipType.TEAM1,
        start_label=NodeType.Match,
        end_label=NodeType.Team,
    ),
    RelationshipType.TEAM2: RelationshipEntry(
        relationship_type=RelationshipType.TEAM2,
        start_label=NodeType.Match,
        end_label=NodeType.Team,
    ),
    RelationshipType.PLAYED_AT: RelationshipEntry(
        relationship_type=RelationshipType.PLAYED_AT,
        start_label=NodeType.Match,
        end_label=NodeType.Venue,
    ),
    RelationshipType.IN_COMPETITION: RelationshipEntry(
        relationship_type=RelationshipType.IN_COMPETITION,
        start_label=NodeType.Match,
        end_label=NodeType.Competition,
    ),
    RelationshipType.OCCURRED_IN: RelationshipEntry(
        relationship_type=RelationshipType.OCCURRED_IN,
        start_label=NodeType.Goal,
        end_label=NodeType.Match,
    ),
    RelationshipType.SCORED_BY: RelationshipEntry(
        relationship_type=RelationshipType.SCORED_BY,
        start_label=NodeType.Goal,
        end_label=NodeType.Player,
    ),
    RelationshipType.CREDITED_TO: RelationshipEntry(
        relationship_type=RelationshipType.CREDITED_TO,
        start_label=NodeType.Goal,
        end_label=NodeType.Team,
    ),
    RelationshipType.CONCEDED_BY: RelationshipEntry(
        relationship_type=RelationshipType.CONCEDED_BY,
        start_label=NodeType.Goal,
        end_label=NodeType.Team,
    ),
}
