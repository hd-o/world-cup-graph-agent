import pytest
from evals.enrichment.evaluation import EnrichmentJudge, create_enrichment_judge
from evals.settings import EvalSettings


@pytest.fixture(name="judge", scope="session")
def create_judge(settings: EvalSettings) -> EnrichmentJudge:
    return create_enrichment_judge(settings)
