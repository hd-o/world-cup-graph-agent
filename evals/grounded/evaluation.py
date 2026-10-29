from evals.evidence import (
    collect_successful_graph_results,
    collect_tool_runs,
    extract_final_answer_text,
    serialize_graph_rows,
)
from evals.facts import assert_required_facts
from langchain_core.messages import BaseMessage
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler


def assert_grounded_facts(
    messages: list[BaseMessage],
    required_facts: list[str],
    run_collector: RunCollectorCallbackHandler,
) -> None:
    """Require a read-only answer supported by successful graph evidence."""
    # Check actual execution, not only messages returned by the supervisor.
    for tool_name in ("research_web", "mutate_graph"):
        assert not collect_tool_runs(run_collector, tool_name), (
            f"{tool_name} executed during a grounded read"
        )

    answer = extract_final_answer_text(messages)
    assert_required_facts(answer, required_facts, "Final answer")

    # Correct facts alone do not prove grounding: they must occur in rows too.
    graph_results = collect_successful_graph_results(messages)
    assert graph_results, "No successful query_graph evidence reached the supervisor"
    graph_rows_json = serialize_graph_rows(graph_results)
    assert_required_facts(graph_rows_json, required_facts, "Successful graph rows")
