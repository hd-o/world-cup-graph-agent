from pydantic import BaseModel, ConfigDict
from world_cup_graph_agent.graph.schema.model.identity import (
    IdentityText,
    normalize_identity_text,
    require_identity_input,
)


class GroupCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    competition_id: str
    name: str


def build_group_id(data: GroupCreation) -> IdentityText:
    group_name = require_identity_input("Group", "name", data.name)

    # Group names such as "Group A" repeat in every competition.
    return f"{data.competition_id}:{normalize_identity_text(group_name)}"
