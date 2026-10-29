import structlog
from collections.abc import Awaitable, Callable
from evals.evidence import (
    collect_successful_graph_results,
    collect_tool_runs,
    extract_final_answer_text,
    serialize_graph_rows,
)
from evals.facts import assert_required_facts
from evals.settings import EvalSettings
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from langsmith import testing
from openevals.llm import create_async_llm_as_judge
from openevals.types import EvaluatorResult
from typing import cast
from world_cup_graph_agent.llm.openrouter import build_chat_model
from world_cup_graph_agent.tools.mutate_graph.models import MutateGraphSuccess


logger = structlog.get_logger(__name__)

type EnrichmentJudge = Callable[..., Awaitable[EvaluatorResult]]

# Exact checks establish completion and tool/database effects. This rubric
# assesses only the prose, keeping the same metric across both conversations.
ENRICHMENT_CORRECTNESS_PROMPT = """
Score the final answer to an enrichment conversation with a binary pass.
Pass only when all applicable conditions hold:
- The answer directly and sufficiently addresses the original question and
  expected outcome; empty, evasive, irrelevant, or fabricated answers fail.
- Every factual claim is supported by the final graph rows and none contradicts
  them. An expected factual answer with empty graph rows fails.
- A rejected outcome must acknowledge the rejection and must not claim that
  research or enrichment succeeded. Empty graph rows are acceptable for this
  non-factual outcome.

<input>{inputs}</input>
<output>{outputs}</output>
<reference_outputs>{reference_outputs}</reference_outputs>
<context>{context}</context>
"""


def create_enrichment_judge(settings: EvalSettings) -> EnrichmentJudge:
    model = build_chat_model(
        settings.openrouter_judge_model_id,
        settings.openrouter_judge_providers,
        reasoning_effort=settings.openrouter_judge_reasoning_effort,
        role="judge",
        openrouter_api_key=settings.openrouter_api_key,
        temperature=0,
        sort=settings.openrouter_judge_sort,
    )

    return cast(
        EnrichmentJudge,
        create_async_llm_as_judge(
            prompt=ENRICHMENT_CORRECTNESS_PROMPT,
            feedback_key="enrichment_correct",
            judge=model,
            continuous=False,
            use_reasoning=True,
        ),
    )


def assert_approved_enrichment(
    messages: list[BaseMessage],
    run_collector: RunCollectorCallbackHandler,
    required_facts: list[str],
) -> None:
    """Require research, an applied mutation, and a fresh supervisor graph read."""
    research_runs = collect_tool_runs(run_collector, "research_web")
    assert research_runs, "Approved enrichment never executed research_web"

    mutation_runs = collect_tool_runs(run_collector, "mutate_graph")
    assert mutation_runs, "Approved enrichment never executed mutate_graph"

    mutation_run = mutation_runs[-1]
    assert mutation_run.outputs is not None, "No mutation result was captured"

    mutation_message = mutation_run.outputs["output"]
    assert isinstance(mutation_message, ToolMessage), "Expected a mutation tool result"

    MutateGraphSuccess.model_validate_json(mutation_message.text)

    assert any(
        run.end_time is not None and run.end_time <= mutation_run.start_time
        for run in research_runs
    ), "Mutation started without a completed research call"

    assert mutation_run.end_time is not None, "Mutation did not finish"

    post_write_call_ids = set()

    for run in collect_tool_runs(run_collector, "query_graph"):
        if run.start_time < mutation_run.end_time or run.outputs is None:
            continue
        message = run.outputs["output"]
        if isinstance(message, ToolMessage):
            post_write_call_ids.add(message.tool_call_id)

    # Only supervisor messages count: the enricher's reread cannot substitute
    # for the supervisor checking the facts used in its final answer.
    supervisor_messages = [
        message
        for message in messages
        if isinstance(message, ToolMessage)
        and message.tool_call_id in post_write_call_ids
    ]

    graph_results = collect_successful_graph_results(supervisor_messages)

    assert graph_results, (
        "Supervisor did not complete a successful post-mutation graph read"
    )

    assert_required_facts(
        serialize_graph_rows(graph_results),
        required_facts,
        "Post-mutation supervisor rows",
    )


async def assert_enrichment_answer(
    judge: EnrichmentJudge,
    question: str,
    messages: list[BaseMessage],
    expected_outcome: str,
) -> None:
    """Judge final prose against the expected outcome and supervisor evidence."""
    outputs = {"answer": extract_final_answer_text(messages)}
    testing.log_outputs(outputs)
    graph_results = collect_successful_graph_results(messages)
    final_graph_rows_json = serialize_graph_rows(graph_results[-1:])

    # OpenEvals traces and logs feedback through its native pytest integration.
    # Retry only its empty/scoreless completion signatures, never a low score.
    for attempt in range(1, 4):
        logger.info("eval_judge_started", attempt=attempt)
        try:
            feedback = await judge(
                inputs={"question": question},
                outputs=outputs,
                reference_outputs={"expected_outcome": expected_outcome},
                context={"final_graph_rows": final_graph_rows_json},
            )
        except TypeError, KeyError:
            if attempt == 3:
                raise

            logger.info("judge_malformed_completion_retry", attempt=attempt)
            continue

        logger.info("eval_judge_feedback", attempt=attempt, score=feedback["score"])
        assert feedback["score"] == 1, feedback.get("comment")
        return
