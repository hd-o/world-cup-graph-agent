from collections.abc import Sequence
from typing import Any


def attach_openrouter_costs(
    run_operations: Sequence[dict[str, Any]],
) -> Sequence[dict[str, Any]]:
    """Attach billed costs to serialized eval messages, preserving runtime usage."""
    priced_operations = []

    for run_operation in run_operations:
        outputs = run_operation.get("outputs") or {}

        if run_operation.get("run_type") != "llm" or "generations" not in outputs:
            priced_operations.append(run_operation)
            continue

        # OpenRouter repeats request-wide usage on each completion choice.
        # Count the charge once per request, not once per choice.
        generations = [
            [_attach_openrouter_cost(batch[0]), *batch[1:]] if batch else batch
            for batch in outputs["generations"]
        ]

        priced_operations.append(
            {
                **run_operation,
                "outputs": {**outputs, "generations": generations},
            }
        )

    return priced_operations


def _attach_openrouter_cost(generation: dict[str, Any]) -> dict[str, Any]:
    message = generation.get("message") or {}
    arguments = message.get("kwargs") or {}
    response_metadata = arguments.get("response_metadata") or {}
    cost = response_metadata.get("cost")

    if response_metadata.get("model_provider") != "openrouter" or cost is None:
        return generation

    # LangSmith reads costs from serialized message usage. Enrich only the
    # export copy, after LangChain has finished aggregating streaming chunks.
    usage = {**(arguments.get("usage_metadata") or {}), "total_cost": cost}

    return {
        **generation,
        "message": {
            **message,
            "kwargs": {**arguments, "usage_metadata": usage},
        },
    }
