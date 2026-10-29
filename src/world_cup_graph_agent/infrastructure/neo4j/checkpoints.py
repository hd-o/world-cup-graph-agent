import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from langchain_core.runnables import RunnableConfig
from langchain_neo4j import AsyncNeo4jSaver
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from neo4j import AsyncDriver, AsyncManagedTransaction
from typing import Any
from uuid import uuid4


class CheckpointConflict(RuntimeError):
    pass


class CheckpointSaver(AsyncNeo4jSaver):
    """Override library behavior needed for private conversations and approvals.

    AsyncNeo4jSaver provides all these methods, but its implementation lacks
    checkpoint/write ordering and task-slot replacement, generates colliding
    channel versions across threads, and omits state from history listings. These
    overrides address those library limitations, not application approval rules.
    """

    def __init__(self, driver: AsyncDriver, database: str) -> None:
        super().__init__(driver, database=database)
        self.readiness_by_checkpoint_id: dict[str, asyncio.Event] = {}

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Override checkpoint saving to signal completion to waiting task writes.

        AsyncNeo4jSaver.aput saves the checkpoint but does not coordinate with
        aput_writes. Its shared lock serializes calls without guaranteeing that
        creation runs first. We retain the parent's storage logic and add the
        signal our aput_writes needs, releasing waiters on failure as well.
        """
        readiness = self.readiness_by_checkpoint_id.setdefault(
            checkpoint["id"], asyncio.Event()
        )

        try:
            return await super().aput(config, checkpoint, metadata, new_versions)
        finally:
            # Also unblock writes when checkpoint creation fails; the graph
            # receives the creation error instead of hanging on pending writes.
            readiness.set()

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Store one durable batch using LangGraph's task-slot semantics.

        AsyncNeo4jSaver.aput_writes uses MATCH on the checkpoint without waiting
        for creation or checking whether it matched. Concurrent writes can
        therefore disappear silently, including approval interrupt IDs. We wait
        for its checkpoint, checking storage first to also support checkpoints
        persisted by another saver instance. Mutable special slots replace their
        current value; ordinary slots retain their first completed write.
        """
        checkpoint_id = config["configurable"]["checkpoint_id"]

        readiness = self.readiness_by_checkpoint_id.setdefault(
            checkpoint_id, asyncio.Event()
        )

        if not readiness.is_set():
            # LangGraph submits checkpoint creation and task writes concurrently.
            # The Neo4j saver matches an existing checkpoint when saving writes;
            # submitting writes first silently loses the pending approval ID.
            async with self._driver.session(database=self._database) as session:
                result = await session.run(
                    """
                    MATCH (checkpoint:Checkpoint {checkpoint_id: $checkpoint_id})
                    RETURN checkpoint.checkpoint_id AS identity
                    """,
                    checkpoint_id=checkpoint_id,
                )

                if await result.single() is not None:
                    readiness.set()

            await readiness.wait()

        records: list[dict[str, Any]] = []
        for index, (channel, value) in enumerate(writes):
            kind, payload = self.serde.dumps_typed(value)
            records.append(
                {
                    "idx": WRITES_IDX_MAP.get(channel, index),
                    "channel": channel,
                    "type": "serde",
                    "blob": json.dumps(
                        {"__serde_type__": kind, "__serde_data__": payload.hex()}
                    ),
                }
            )

        async def store_writes(transaction: AsyncManagedTransaction) -> None:
            # Reserved channels cannot occupy an ordinary write's index.
            # Preserve every record/value while aligning its physical slot.
            result = await transaction.run(
                """
                MATCH (checkpoint:Checkpoint {checkpoint_id: $checkpoint_id})
                      -[:HAS_WRITE]->(write:PendingWrite {task_id: $task_id})
                WHERE write.channel IN keys($indices)
                SET write.idx = $indices[write.channel]
                """,
                checkpoint_id=checkpoint_id,
                task_id=task_id,
                indices=WRITES_IDX_MAP,
            )
            await result.consume()
            for record in records:
                if record["idx"] < 0:
                    query = """
                    MATCH (checkpoint:Checkpoint {checkpoint_id: $checkpoint_id})
                    MERGE (checkpoint)-[:HAS_WRITE]->(write:PendingWrite {
                        task_id: $task_id, channel: $channel
                    })
                    SET write.idx = $idx, write.task_path = $task_path,
                        write.type = $type, write.blob = $blob
                    RETURN count(write) AS count
                    """
                else:
                    query = """
                    MATCH (checkpoint:Checkpoint {checkpoint_id: $checkpoint_id})
                    MERGE (checkpoint)-[:HAS_WRITE]->(write:PendingWrite {
                        task_id: $task_id, idx: $idx
                    })
                    ON CREATE SET write.channel = $channel,
                        write.task_path = $task_path,
                        write.type = $type, write.blob = $blob
                    RETURN count(write) AS count
                    """
                result = await transaction.run(
                    query,
                    checkpoint_id=checkpoint_id,
                    task_id=task_id,
                    task_path=task_path,
                    **record,
                )
                count = await result.single(strict=True)
                if count is None or count["count"] == 0:
                    raise CheckpointConflict(
                        "Checkpoint unavailable for pending writes"
                    )

        # LangGraph task slots keep ordinary writes immutable and replace
        # special channels (interrupt/resume/error) at their reserved indices.
        # Appending interrupts exposes consumed approvals as pending actions.
        # One transaction preserves the complete write batch on failure/retry.
        async with (
            self._lock,
            self._driver.session(database=self._database) as session,
        ):
            await session.execute_write(store_writes)

    def _load_writes(
        self, write_records: list[dict[str, Any]]
    ) -> list[tuple[str, str, Any]]:
        values = super()._load_writes(write_records)
        writes_by_slot = {}
        for record, value in zip(write_records, values, strict=True):
            slot = (
                record["task_id"],
                WRITES_IDX_MAP.get(record["channel"], record["idx"]),
            )
            if slot in writes_by_slot and writes_by_slot[slot] != value:
                # Ambiguous saved progress cannot establish replay permission.
                # Keep all stored records intact for operator inspection.
                raise CheckpointConflict("Conflicting saved task-slot values")
            writes_by_slot[slot] = value
        return list(writes_by_slot.values())

    def get_next_version(
        self, current: str | None, channel: ChannelVersions | None
    ) -> str:
        """Override version generation to avoid cross-conversation state collisions.

        The inherited generator increments a counter with a fixed zero suffix,
        so separate threads generate identical versions. Neo4j merges channel
        state by (channel, version), without a thread or namespace, which can
        reuse another conversation's values. We retain counter ordering and
        add a UUID suffix to make collisions unlikely.
        """
        # Neo4j ChannelState uniqueness covers only (channel, version), not
        # thread or namespace. Keep sequence ordering and add a unique suffix
        # so conversations and subgraphs cannot reuse each other's state.
        version = 1 if current is None else int(current.split(".", 1)[0]) + 1
        return f"{version:032}.{uuid4().int:039}"

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """Override history listing to include state and pending approval interrupts.

        AsyncNeo4jSaver.alist deliberately omits channel values and pending
        writes; its aget_tuple loads both. History consumers such as Studio need
        those fields to display state and approvals, so we reload each listed
        checkpoint. Listing must finish first because both parent methods use
        the same lock, and alist holds it across yields.
        """
        # History needs pending writes to show approval interrupts. The parent
        # iterator holds its lock across yields, so finish listing before
        # aget_tuple acquires that same lock to load complete checkpoints.
        configs = [
            checkpoint.config
            async for checkpoint in super().alist(
                config, filter=filter, before=before, limit=limit
            )
        ]

        for checkpoint_config in configs:
            checkpoint = await self.aget_tuple(checkpoint_config)
            if checkpoint is not None:
                yield checkpoint
