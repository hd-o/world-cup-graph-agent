from langchain_core.runnables import RunnableConfig
from langsmith import get_current_run_tree, testing


def record_example(
    question: str, reference_outputs: dict, config: RunnableConfig
) -> None:
    """Record scenario data, not the runtime fixtures in pytest's signature."""
    scenario_trace = get_current_run_tree()
    metadata = config["metadata"]

    # The SDK's input logger merges keys. Clear the captured fixture mapping
    # before logging so dataset examples contain only reproducible inputs.
    if scenario_trace is not None:
        scenario_trace.inputs.clear()
        scenario_trace.add_metadata(metadata)
        scenario_trace.add_tags(config["tags"])

    # Approval and rejection ask the same question but are distinct examples.
    testing.log_inputs({"scenario": metadata["eval_scenario"], "question": question})
    testing.log_reference_outputs(reference_outputs)
