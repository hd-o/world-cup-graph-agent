from pydantic import BaseModel, ConfigDict
from world_cup_graph_agent.graph.schema.model.identity import (
    IdentityText,
    normalize_identity_text,
    require_identity_input,
)


class ClubCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    country: str


def build_club_id(data: ClubCreation) -> IdentityText:
    club_name = require_identity_input("Club", "name", data.name)
    country_name = require_identity_input("Club", "country", data.country)

    # Club names can be reused in different countries.
    return (
        f"{normalize_identity_text(club_name)}:{normalize_identity_text(country_name)}"
    )
