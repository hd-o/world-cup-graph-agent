import json
from collections.abc import Iterator, Sequence
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from langchain_core.tracers.schemas import Run
from pydantic import ValidationError
from world_cup_graph_agent.tools.query_graph import QueryGraphSuccess


def extract_final_answer_text(messages: Sequence[BaseMessage]) -> str:
    """Read the final visible assistant answer, skipping empty completions."""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.text.strip():
            return message.text.strip()

    return ""


def collect_successful_graph_results(
    messages: Sequence[BaseMessage],
) -> list[QueryGraphSuccess]:
    """Read valid graph evidence visible to the supervisor."""
    graph_results = []

    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "query_graph":
            continue

        if message.status == "error":
            continue

        # Failed or malformed reads are not evidence. A later repaired query
        # can still supply valid rows, using the application's result schema.
        try:
            graph_results.append(QueryGraphSuccess.model_validate_json(message.text))
        except ValidationError:
            continue

    return graph_results


def serialize_graph_rows(graph_results: list[QueryGraphSuccess]) -> str:
    rows = [row for graph_result in graph_results for row in graph_result.rows]
    return json.dumps(rows, ensure_ascii=False)


def collect_tool_runs(
    run_collector: RunCollectorCallbackHandler, tool_name: str
) -> list[Run]:
    """Find executed tools, including tools inside isolated subagents."""
    # A missing callback must fail the check, not look like no tools executed.
    assert run_collector.traced_runs, "No conversation execution was captured"

    runs = [
        run
        for root in run_collector.traced_runs
        for run in _iterate_runs(root)
        if run.run_type == "tool" and run.name == tool_name
    ]

    return sorted(runs, key=lambda run: run.start_time)


def _iterate_runs(run: Run) -> Iterator[Run]:
    yield run
    for child in run.child_runs:
        yield from _iterate_runs(child)
