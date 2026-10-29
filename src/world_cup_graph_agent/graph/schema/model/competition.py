from pydantic import BaseModel, ConfigDict
from world_cup_graph_agent.graph.schema.model.identity import (
    IdentityText,
    normalize_identity_text,
    require_identity_input,
)


class CompetitionCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    edition_id: str
    competition_type: str


def build_competition_id(data: CompetitionCreation) -> IdentityText:
    competition_type_text = require_identity_input(
        "Competition",
        "competition_type",
        data.competition_type,
    )

    # Competition names such as "finals" repeat across editions, so the edition
    # stays part of their identity.
    return f"{data.edition_id}:{normalize_identity_text(competition_type_text)}"
