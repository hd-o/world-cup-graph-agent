import json
from pydantic import BaseModel, ConfigDict
from world_cup_graph_agent.graph.schema.model.identity import (
    IdentityText,
    require_identity_input,
)


class GoalCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    match_id: str
    minute: str | None = None
    minute_base: int | None = None
    minute_stoppage: int | None = None
    penalty: bool
    own_goal: bool
    scorer_id: str | None = None
    scorer_name: str | None = None
    occurrence_index: int


def build_goal_id(data: GoalCreation) -> IdentityText:
    """Build a stable ID and disambiguate otherwise identical goal events.

    Some source matches contain multiple goals with the same minute, scorer, and
    flags. `occurrence_index` keeps those legitimate events as separate nodes.
    """
    required_match_id = require_identity_input("Goal", "match_id", data.match_id)

    # Only facts intrinsic to the source event feed identity, so later
    # enrichment cannot change identity and split one Goal into two nodes.
    identity_fields = {
        "minute": data.minute,
        "own_goal": data.own_goal,
        "penalty": data.penalty,
        "scorer_id": data.scorer_id,
    }

    # Stable key ordering and compact separators make the same identity fields
    # produce exactly the same ID on every run.
    serialized_identity_fields = json.dumps(
        identity_fields,
        sort_keys=True,
        separators=(",", ":"),
    )

    return f"{required_match_id}:{serialized_identity_fields}:{data.occurrence_index}"
