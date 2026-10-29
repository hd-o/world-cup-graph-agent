from pydantic import BaseModel, ConfigDict
from world_cup_graph_agent.graph.schema.model.identity import (
    IdentityText,
    require_identity_input,
)


class EditionCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tournament_id: str
    year: int
    name: str


class EditionFill(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = None


def build_edition_id(data: EditionCreation) -> IdentityText:
    year_text = require_identity_input("Edition", "year", data.year)

    # An edition belongs to its parent tournament rather than to a bare year
    # shared by many football competitions.
    return f"{data.tournament_id}:{year_text}"
