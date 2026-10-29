from pydantic import BaseModel, ConfigDict
from world_cup_graph_agent.graph.schema.model.identity import (
    IdentityText,
    require_identity_input,
)


class MatchCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    competition_id: str
    date: str
    team1_id: str
    team2_id: str
    round: str | None = None
    time: str | None = None
    score_team1: int | None = None
    score_team2: int | None = None
    score_halftime_team1: int | None = None
    score_halftime_team2: int | None = None
    score_fulltime_team1: int | None = None
    score_fulltime_team2: int | None = None
    score_extratime_team1: int | None = None
    score_extratime_team2: int | None = None
    score_penalties_team1: int | None = None
    score_penalties_team2: int | None = None
    knockout: bool | None = None
    weather_temperature_celsius: float | None = None


class MatchFill(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    round: str | None = None
    time: str | None = None
    score_team1: int | None = None
    score_team2: int | None = None
    score_halftime_team1: int | None = None
    score_halftime_team2: int | None = None
    score_fulltime_team1: int | None = None
    score_fulltime_team2: int | None = None
    score_extratime_team1: int | None = None
    score_extratime_team2: int | None = None
    score_penalties_team1: int | None = None
    score_penalties_team2: int | None = None
    knockout: bool | None = None
    weather_temperature_celsius: float | None = None


def build_match_id(data: MatchCreation) -> IdentityText:
    date_text = require_identity_input("Match", "date", data.date)
    team1_id = require_identity_input("Match", "team1_id", data.team1_id)
    team2_id = require_identity_input("Match", "team2_id", data.team2_id)

    # Date and teams identify a fixture; the competition keeps fixtures of
    # different competitions in separate identity scopes.
    return f"{data.competition_id}:{date_text}:{team1_id}:{team2_id}"
