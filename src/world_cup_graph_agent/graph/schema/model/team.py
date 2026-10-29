from pydantic import BaseModel, ConfigDict
from world_cup_graph_agent.graph.schema.model.identity import (
    IdentityText,
    normalize_identity_text,
    require_identity_input,
)


class TeamCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fifa_code: str
    name: str
    confederation: str | None = None


class TeamFill(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = None
    confederation: str | None = None


def build_team_id(data: TeamCreation) -> IdentityText:
    code = require_identity_input("Team", "fifa_code", data.fifa_code)

    # FIFA codes are conventionally uppercase; accepting either input casing
    # still produces one Team ID.
    return normalize_identity_text(code).upper()
