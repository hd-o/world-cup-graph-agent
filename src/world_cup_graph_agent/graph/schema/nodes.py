from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pydantic import BaseModel
from world_cup_graph_agent.graph.schema.model.club import ClubCreation, build_club_id
from world_cup_graph_agent.graph.schema.model.competition import (
    CompetitionCreation,
    build_competition_id,
)
from world_cup_graph_agent.graph.schema.model.edition import (
    EditionCreation,
    EditionFill,
    build_edition_id,
)
from world_cup_graph_agent.graph.schema.model.goal import GoalCreation, build_goal_id
from world_cup_graph_agent.graph.schema.model.group import GroupCreation, build_group_id
from world_cup_graph_agent.graph.schema.model.match import (
    MatchCreation,
    MatchFill,
    build_match_id,
)
from world_cup_graph_agent.graph.schema.model.player import (
    PlayerCreation,
    PlayerFill,
    build_player_id,
)
from world_cup_graph_agent.graph.schema.model.team import (
    TeamCreation,
    TeamFill,
    build_team_id,
)
from world_cup_graph_agent.graph.schema.model.venue import (
    VenueCreation,
    VenueFill,
    build_venue_id,
)


class NodeType(StrEnum):
    Tournament = "Tournament"
    Edition = "Edition"
    Competition = "Competition"
    Group = "Group"
    Team = "Team"
    Player = "Player"
    Club = "Club"
    Match = "Match"
    Venue = "Venue"
    Goal = "Goal"


@dataclass(frozen=True)
class NodeEntry:
    label: str
    create: type[BaseModel] | None
    fill: type[BaseModel] | None
    build_id: Callable[..., str] | None


NODES: dict[NodeType, NodeEntry] = {
    NodeType.Tournament: NodeEntry(
        label="Tournament",
        create=None,
        # The tournament name feeds the deterministic ID, so no fillable
        # property remains on the bootstrap-owned root.
        fill=None,
        build_id=None,
    ),
    NodeType.Edition: NodeEntry(
        label="Edition",
        create=EditionCreation,
        fill=EditionFill,
        build_id=build_edition_id,
    ),
    NodeType.Competition: NodeEntry(
        label="Competition",
        create=CompetitionCreation,
        # Every persisted Competition property feeds the deterministic ID,
        # so no fillable property remains.
        fill=None,
        build_id=build_competition_id,
    ),
    NodeType.Group: NodeEntry(
        label="Group",
        create=GroupCreation,
        # Every persisted Group property feeds the deterministic ID,
        # so no fillable property remains.
        fill=None,
        build_id=build_group_id,
    ),
    NodeType.Team: NodeEntry(
        label="Team",
        create=TeamCreation,
        fill=TeamFill,
        build_id=build_team_id,
    ),
    NodeType.Player: NodeEntry(
        label="Player",
        create=PlayerCreation,
        fill=PlayerFill,
        build_id=build_player_id,
    ),
    NodeType.Club: NodeEntry(
        label="Club",
        create=ClubCreation,
        # Every persisted Club property feeds the deterministic ID,
        # so no fillable property remains.
        fill=None,
        build_id=build_club_id,
    ),
    NodeType.Match: NodeEntry(
        label="Match",
        create=MatchCreation,
        fill=MatchFill,
        build_id=build_match_id,
    ),
    NodeType.Venue: NodeEntry(
        label="Venue",
        create=VenueCreation,
        fill=VenueFill,
        build_id=build_venue_id,
    ),
    NodeType.Goal: NodeEntry(
        label="Goal",
        create=GoalCreation,
        # A Goal is created complete from its source event, so no property
        # is fillable afterwards.
        fill=None,
        build_id=build_goal_id,
    ),
}


def list_node_labels() -> list[str]:
    return [entry.label for entry in NODES.values()]


def list_node_property_keys() -> list[str]:
    # Create-model fields are all persisted properties, and fill fields are a
    # subset of create fields; `id` never appears in a model because handlers
    # build it, so it joins the set literally.
    keys = {"id"}

    for entry in NODES.values():
        if entry.create is not None:
            keys.update(entry.create.model_fields)

    return sorted(keys)
