import pytest
from aiomisc import timeout
from evals.enrichment.approvals import (
    ApprovalDecision,
    complete_approved_enrichment,
    request_research_approval,
    send_message,
    submit_approval_decision,
)
from evals.enrichment.database import read_mbappe_birthplace
from evals.enrichment.evaluation import (
    EnrichmentJudge,
    assert_approved_enrichment,
    assert_enrichment_answer,
)
from evals.evidence import collect_tool_runs
from evals.facts import assert_required_facts
from evals.reporting.examples import record_example
from evals.runtime import ConversationRunner
from evals.settings import EvalSettings
from langchain_core.runnables import RunnableConfig
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from world_cup_graph_agent.infrastructure.neo4j.driver import GraphDrivers


pytestmark = [
    pytest.mark.enrichment,
    pytest.mark.langsmith(test_suite_name="worldcup-agent-evals"),
]


@timeout(600)
async def test_rejected_birthplace_research(
    conversation_runner: ConversationRunner,
    config: RunnableConfig,
    settings: EvalSettings,
    drivers: GraphDrivers,
    run_collector: RunCollectorCallbackHandler,
    judge: EnrichmentJudge,
) -> None:
    question = "What is Mbappe's birthplace?"

    expected_outcome = """
    Acknowledge that the user rejected research_web and report research_rejected.
    Do not claim research or enrichment succeeded or invent Mbappe's birthplace.
    """

    record_example(question, {"expected_outcome": expected_outcome}, config)
    birthplace_before = await read_mbappe_birthplace(settings, drivers.reader)
    assert birthplace_before is None, "Scenario requires a missing birthplace"
    conversation_state = await send_message(conversation_runner, config, question)

    conversation_state = await request_research_approval(
        conversation_runner, config, conversation_state
    )

    conversation_state = await submit_approval_decision(
        conversation_runner, config, conversation_state, ApprovalDecision.REJECT
    )

    assert not conversation_state.get("__interrupt__"), (
        "Rejected enrichment did not finish"
    )

    assert not collect_tool_runs(run_collector, "research_web"), (
        "Research executed despite rejection"
    )

    assert not collect_tool_runs(run_collector, "mutate_graph"), (
        "Mutation executed despite rejected research"
    )

    birthplace_after = await read_mbappe_birthplace(settings, drivers.reader)
    assert birthplace_after == birthplace_before, "Rejected research changed the graph"

    await assert_enrichment_answer(
        judge, question, conversation_state["messages"], expected_outcome
    )


@timeout(600)
async def test_approved_birthplace_enrichment(
    conversation_runner: ConversationRunner,
    config: RunnableConfig,
    settings: EvalSettings,
    drivers: GraphDrivers,
    run_collector: RunCollectorCallbackHandler,
    judge: EnrichmentJudge,
) -> None:
    question = "What is Mbappe's birthplace?"

    expected_outcome = """
    Answer Mbappe's birthplace as Paris, France from the supervisor's graph rows.
    Do not add factual claims supported only by the enrichment report or research.
    """

    # The birthplace property can contain just a city; country wording belongs
    # to semantic answer evaluation, not the storage format.
    required_facts = ["Paris"]

    record_example(question, {"expected_outcome": expected_outcome}, config)
    birthplace_before = await read_mbappe_birthplace(settings, drivers.reader)
    assert birthplace_before is None, "Scenario requires a missing birthplace"

    conversation_state = await send_message(conversation_runner, config, question)

    conversation_state = await request_research_approval(
        conversation_runner, config, conversation_state
    )

    conversation_state = await complete_approved_enrichment(
        conversation_runner, config, conversation_state
    )

    assert not conversation_state.get("__interrupt__"), (
        "Approved enrichment did not finish"
    )

    birthplace_after = await read_mbappe_birthplace(settings, drivers.reader)
    assert_required_facts(birthplace_after or "", required_facts, "Stored birthplace")

    assert_approved_enrichment(
        conversation_state["messages"], run_collector, required_facts
    )

    await assert_enrichment_answer(
        judge, question, conversation_state["messages"], expected_outcome
    )
