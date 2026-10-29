import pytest
from aiomisc import timeout
from evals.evidence import extract_final_answer_text
from evals.grounded.evaluation import assert_grounded_facts
from evals.reporting.examples import record_example
from evals.runtime import ConversationRunner
from langchain_core.runnables import RunnableConfig
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from langsmith import testing


pytestmark = [
    pytest.mark.grounded,
    pytest.mark.langsmith(
        output_keys=["required_facts"], test_suite_name="worldcup-agent-evals"
    ),
]


# Every reference fact must appear in both the final answer and a successful
# query_graph read, so these examples check correctness and grounding together.
@pytest.mark.parametrize(
    ("question", "required_facts"),
    [
        pytest.param(
            "What is the FIFA code for France?",
            ["FRA"],
            id="grounded_fifa_code",
            marks=pytest.mark.fast,
        ),
        pytest.param(
            "Which teams share a group with Brazil?",
            ["Morocco", "Haiti", "Scotland"],
            id="grounded_brazil_groups",
        ),
        pytest.param(
            "List the strikers in the France squad.",
            # Surnames keep matching robust to full-name phrasing. All seven seeded
            # forwards fit in one 25-row display page, so the full list is required.
            ["Dembele", "Thuram", "Mbappe", "Olise", "Barcola", "Doue", "Mateta"],
            id="grounded_france_strikers",
        ),
        pytest.param(
            "who won the worldcup?",
            ["Spain"],
            id="grounded_worldcup_winner",
        ),
    ],
)
@timeout(600)
async def test_grounded_answer(
    question: str,
    required_facts: list[str],
    conversation_runner: ConversationRunner,
    config: RunnableConfig,
    run_collector: RunCollectorCallbackHandler,
) -> None:
    record_example(question, {"required_facts": required_facts}, config)

    conversation_state = await conversation_runner(
        {"messages": [{"role": "user", "content": question}]}, config
    )

    assert not conversation_state.get("__interrupt__"), (
        "Grounded answer ended with a pending approval"
    )

    messages = conversation_state["messages"]
    testing.log_outputs(
        {"answer": extract_final_answer_text(messages), "messages": messages}
    )
    assert_grounded_facts(messages, required_facts, run_collector)
