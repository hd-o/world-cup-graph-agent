from pydantic import BaseModel, ConfigDict
from typing import Literal


class GraphEnrichmentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal[
        "enriched",
        "no_change",
        "research_rejected",
        "mutation_rejected",
        "failed",
    ]

    summary: str
    changed_ids: list[str]
    failure: str | None = None
