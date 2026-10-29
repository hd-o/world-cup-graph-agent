from pydantic import BaseModel, ConfigDict
from world_cup_graph_agent.graph.schema.model.identity import (
    IdentityText,
    normalize_identity_text,
    require_identity_input,
)


class PlayerCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    date_of_birth: str
    birthplace: str | None = None


class PlayerFill(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    birthplace: str | None = None


def build_player_id(data: PlayerCreation) -> IdentityText:
    player_name = require_identity_input("Player", "name", data.name)
    normalized_name = normalize_identity_text(player_name)
    birth = require_identity_input("Player", "date_of_birth", data.date_of_birth)

    # Names are not unique; birth date distinguishes players who share a name.
    return f"{normalized_name}:{birth}"
