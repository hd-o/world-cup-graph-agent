from pydantic import BaseModel, ValidationError
from world_cup_graph_agent.tools.mutate_graph.models import (
    MutationBatchError,
    MutationOperation,
)


def require_fields(
    index: int,
    flat_operation: MutationOperation,
    present: list[str],
    absent: list[str],
    message: str,
) -> None:
    # Each operation carries exactly one flat shape; a missing required
    # field or a field from another shape fails the batch before any write.
    for name in present:
        if getattr(flat_operation, name) is None:
            raise MutationBatchError(index, "validation_error", message)

    for name in absent:
        if getattr(flat_operation, name) is not None:
            raise MutationBatchError(index, "validation_error", message)


def parse_payload[ParsedModel: BaseModel](
    index: int, model: type[ParsedModel], payload: object
) -> ParsedModel:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise MutationBatchError(index, "validation_error", str(error)) from error
