from pydantic import BaseModel, ConfigDict
from world_cup_graph_agent.graph.schema.model.identity import (
    IdentityText,
    normalize_identity_text,
    require_identity_input,
)


class VenueCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    city: str
    country_code: str | None = None
    capacity: int | None = None


class VenueFill(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    country_code: str | None = None
    capacity: int | None = None


def build_venue_id(data: VenueCreation) -> IdentityText:
    venue_name = require_identity_input("Venue", "name", data.name)
    city_name = require_identity_input("Venue", "city", data.city)

    # Venue names can be reused in different cities.
    return f"{normalize_identity_text(venue_name)}:{normalize_identity_text(city_name)}"
