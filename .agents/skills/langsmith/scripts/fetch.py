"""
https://docs.langchain.com/langsmith/smithdb-sdk-migration.md
"""

import argparse
import asyncio
import json
import os
from langsmith import Client
from langsmith._openapi_client.types.run import Run
from langsmith._openapi_client.types.run_select_field import RunSelectField
from typing import Literal


IdKind = Literal["thread", "trace"]

# SmithDB returns only `id` unless selects is set. These fields are the
# debug dump: status, errors, I/O, and tree order. The name is
# PARENT_RUN_IDS (a list of ancestors), not PARENT_RUN_ID.
RUN_SELECTS: list[RunSelectField] = [
    "ID",
    "NAME",
    "RUN_TYPE",
    "STATUS",
    "START_TIME",
    "END_TIME",
    "ERROR",
    "INPUTS",
    "OUTPUTS",
    "PARENT_RUN_IDS",
    "TRACE_ID",
    "DOTTED_ORDER",
    "TAGS",
    "EXTRA",
    "METADATA",
    "TOTAL_TOKENS",
    "TOTAL_COST",
]


def render_json(payload: object) -> str:
    return json.dumps(payload, default=str, ensure_ascii=False)


def preview_value(value: object, max_len: int = 4_000) -> object:
    """Keep one run from blowing up the dump.

    Truncate after JSON encoding so the cut is on text
    size, not Python object shape.
    """
    if value is None:
        return None

    text = json.dumps(value, default=str, ensure_ascii=False)

    if len(text) <= max_len:
        return value

    return text[:max_len] + f"... <truncated {len(text) - max_len} chars>"


async def list_thread_trace_ids(
    client: Client, project_id: str, thread_id: str
) -> list[str]:
    """List one conversation's turns.

    A thread is the chat. Each trace is one user turn. Studio links
    usually give this id. Default selects on list_traces is enough:
    STATUS, ID, and INPUTS are not valid fields here.
    """
    trace_ids: list[str] = []

    async for trace in client.threads.list_traces(thread_id, project_id=project_id):
        if trace.trace_id:
            trace_ids.append(str(trace.trace_id))

    return trace_ids


async def list_trace_runs(client: Client, project_id: str, trace_id: str) -> list[Run]:
    """Load the run tree for one turn.

    Prefer traces.list_runs over runs.query: query defaults min_start_time
    to yesterday and rejects windows over 401 days. list_runs has no
    default time filter. items can be None, so treat that as empty.
    """
    response = await client.traces.list_runs(
        trace_id,
        project_id=project_id,
        selects=RUN_SELECTS,
    )

    runs = list(response.items or [])

    # dotted_order is the tree walk: root first, then children in
    # start order. Empty string keeps unsentinelled runs at the front.
    return sorted(runs, key=lambda run: run.dotted_order or "")


def serialize_run(run: Run) -> dict[str, object]:
    return {
        "id": str(run.id),
        "name": run.name,
        "type": run.run_type,
        "status": run.status,
        "error": run.error,
        "parents": [str(item) for item in (run.parent_run_ids or [])],
        "start": str(run.start_time) if run.start_time else None,
        "end": str(run.end_time) if run.end_time else None,
        "tokens": run.total_tokens,
        "cost": str(run.total_cost) if run.total_cost is not None else None,
        "tags": run.tags,
        "metadata": preview_value(run.metadata),
        "inputs": preview_value(run.inputs),
        "outputs": preview_value(run.outputs),
    }


async def load_thread_runs(
    client: Client, project_id: str, thread_id: str
) -> dict[str, list[Run]]:
    """Dump every turn in a thread.

    Turns are independent traces, so their run trees can load together
    after the turn list is known. This is not guessing thread vs
    trace: the caller already passed kind=thread.
    """
    trace_ids = await list_thread_trace_ids(client, project_id, thread_id)

    if not trace_ids:
        raise SystemExit(f"No traces found for thread {thread_id} in this project")

    fetched_runs = await asyncio.gather(
        *[list_trace_runs(client, project_id, trace_id) for trace_id in trace_ids]
    )

    return {
        trace_id: runs for trace_id, runs in zip(trace_ids, fetched_runs, strict=True)
    }


async def load_trace_runs(
    client: Client, project_id: str, trace_id: str
) -> dict[str, list[Run]]:
    """Dump one turn only.

    traces.query(trace_ids=[thread_id]) returns nothing. A thread id
    must go through list_traces first. This path is for a real trace id.
    """
    runs = await list_trace_runs(client, project_id, trace_id)

    if not runs:
        raise SystemExit(f"No runs found for trace {trace_id} in this project")

    return {trace_id: runs}


async def main() -> None:
    parser = argparse.ArgumentParser()

    # Kind is required so we do not probe both APIs. The two ids look
    # the same (UUIDs). The user or skill already knows which one it is.
    parser.add_argument("kind", choices=["thread", "trace"])
    parser.add_argument("id", help="LangSmith thread id or trace id")
    args = parser.parse_args()
    kind: IdKind = args.kind
    raw_id = args.id.strip()

    project_name = os.environ.get("LANGSMITH_PROJECT")

    if not project_name:
        raise SystemExit("LANGSMITH_PROJECT is not set")

    # SmithDB partitions by project UUID, not the name in .env.
    client = Client()
    project = await client.aread_project(project_name=project_name)
    project_id = str(project.id)

    if kind == "thread":
        runs_by_trace_id = await load_thread_runs(client, project_id, raw_id)
    else:
        runs_by_trace_id = await load_trace_runs(client, project_id, raw_id)

    traces_payload = []

    for trace_id, runs in runs_by_trace_id.items():
        traces_payload.append(
            {
                "trace_id": trace_id,
                "run_count": len(runs),
                "runs": [serialize_run(run) for run in runs],
            }
        )

    print(
        render_json(
            {
                "project": project_name,
                "project_id": project_id,
                "kind": kind,
                "given_id": raw_id,
                "trace_ids": list(runs_by_trace_id),
                "traces": traces_payload,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
