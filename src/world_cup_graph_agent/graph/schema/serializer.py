import json
from annotationlib import Format, get_annotations
from pathlib import Path
from pydantic import BaseModel
from world_cup_graph_agent.graph.schema.nodes import NODES
from world_cup_graph_agent.graph.schema.relationships import RELATIONSHIPS


skill_schema_target = (
    Path(__file__).resolve().parents[2]
    / "skills/querying/world-cup-querying/schema.json"
)


# Fail fast if there's a refactor drift or path change
if not (skill_schema_target.parent / "SKILL.md").is_file():
    raise FileNotFoundError(f"Expected SKILL.md beside {skill_schema_target}")


def write_serialized_schema() -> Path:
    """Write the schema reference next to the querying SKILL.md.

    The graph schema is defined in code. The node and relationship registries
    and their Pydantic models describe every label, relationship type, and
    property. This function writes that definition to `schema.json` so the
    querying skill can show the model what it may read and write before any
    Cypher is generated.

    `poe bootstrap` regenerates the file whenever those registries and
    models change. The schema is fixed for a running build, so a generated
    file is always current. That is why this is not a `get_schema` tool or a
    schema assembled at runtime. Those would add a step to fetch or rebuild
    schema that only changes when the code changes.
    """
    skill_schema_target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(_serialize_schema(), indent=2) + "\n"
    skill_schema_target.write_text(content, encoding="utf-8", newline="\n")
    return skill_schema_target


def _build_property_map(model: type[BaseModel] | None) -> dict[str, str]:
    return {} if model is None else get_annotations(model, format=Format.STRING)


def _serialize_schema() -> dict[str, object]:
    nodes: dict[str, object] = {}

    for node_entry in NODES.values():
        # `id` is a deterministic string on every node label, so it leads the
        # creation map literally: creation models exclude it because handlers
        # build it before persistence.
        creation = {"id": "str"} | _build_property_map(node_entry.create)
        schema: dict[str, object] = {"create": creation}
        fill = _build_property_map(node_entry.fill)

        if fill:
            schema["fill"] = fill

        nodes[node_entry.label] = schema

    relationships: dict[str, object] = {}

    for relationship in RELATIONSHIPS.values():
        schema = {
            "start": NODES[relationship.start_label].label,
            "end": NODES[relationship.end_label].label,
        }

        id_map = {"id": "str"} if relationship.build_id is not None else {}
        creation = id_map | _build_property_map(relationship.create)

        if creation:
            schema["create"] = creation

        fill = _build_property_map(relationship.fill)

        if fill:
            schema["fill"] = fill

        relationships[relationship.relationship_type.value] = schema

    return {"nodes": nodes, "relationships": relationships}
