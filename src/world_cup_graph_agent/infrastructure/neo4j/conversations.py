from neo4j import AsyncDriver
from typing import Literal
from uuid import uuid4
from world_cup_graph_agent.runtimes.contracts import ConversationSummary


class ConversationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ConversationStore:
    def __init__(self, driver: AsyncDriver, database: str) -> None:
        self.driver = driver
        self.database = database

    async def create_conversation(self, owner: str) -> str:
        thread_id = str(uuid4())
        await self.driver.execute_query(
            """
            CREATE (conversation:HostedConversation {
                thread_id: $thread_id, owner: $owner, status: 'idle',
                admission_revision: 0, created_at: datetime()
            })
            """,
            thread_id=thread_id,
            owner=owner,
            database_=self.database,
        )

        return thread_id

    async def list_conversations(
        self, owner: str, limit: int, cursor: str | None
    ) -> list[ConversationSummary]:
        rows, _, _ = await self.driver.execute_query(
            """
            MATCH (conversation:HostedConversation {owner: $owner})
            WHERE $cursor IS NULL OR conversation.thread_id > $cursor
            RETURN conversation.thread_id AS thread_id,
                CASE WHEN conversation.status = 'active'
                    AND conversation.started_at < datetime() - duration('PT10M')
                    THEN 'uncertain' ELSE conversation.status END AS status
            ORDER BY thread_id LIMIT $limit
            """,
            owner=owner,
            cursor=cursor,
            limit=limit,
            database_=self.database,
        )

        return [
            ConversationSummary(thread_id=row["thread_id"], status=row["status"])
            for row in rows
        ]

    async def read_status(self, owner: str, thread_id: str) -> str:
        rows, _, _ = await self.driver.execute_query(
            """
            MATCH (conversation:HostedConversation {
                thread_id: $thread_id, owner: $owner
            })
            RETURN CASE WHEN conversation.status = 'active'
                AND conversation.started_at < datetime() - duration('PT10M')
                THEN 'uncertain' ELSE conversation.status END AS status
            """,
            thread_id=thread_id,
            owner=owner,
            database_=self.database,
        )

        if not rows:
            raise ConversationError("conversation_not_found")

        return rows[0]["status"]

    async def admit_execution(
        self, owner: str, thread_id: str, request_id: str
    ) -> None:
        # Acquire the node's write lock before checking status. Neo4j documents
        # automatic locking for dependent property updates; separate MATCH/SET
        # queries in this explicit transaction prevent check-then-write races.
        # https://neo4j.com/docs/operations-manual/current/database-internals/concurrent-data-access/
        async with (
            self.driver.session(database=self.database) as session,
            await session.begin_transaction() as transaction,
        ):
            admission = await transaction.run(
                """
                    MATCH (conversation:HostedConversation {
                        thread_id: $thread_id, owner: $owner
                    })
                    SET conversation.admission_revision =
                        conversation.admission_revision + 1
                    RETURN conversation.status AS status
                    """,
                thread_id=thread_id,
                owner=owner,
            )

            record = await admission.single()
            if record is None:
                raise ConversationError("conversation_not_found")
            if record["status"] in ("active", "uncertain"):
                raise ConversationError(
                    "execution_conflict"
                    if record["status"] == "active"
                    else "execution_uncertain"
                )

            execution = await transaction.run(
                """
                    MATCH (conversation:HostedConversation {thread_id: $thread_id})
                    SET conversation.status = 'active',
                        conversation.request_id = $request_id,
                        conversation.started_at = datetime()
                    """,
                thread_id=thread_id,
                request_id=request_id,
            )

            await execution.consume()
            await transaction.commit()

    async def finish_execution(
        self,
        thread_id: str,
        request_id: str,
        status: Literal["idle", "uncertain", "approval_required"],
    ) -> None:
        # Completion is fenced to the admitting request. There is no lease expiry
        # or automatic unlock: a dead worker cannot authorize mutation replay.
        rows, _, _ = await self.driver.execute_query(
            """
            MATCH (conversation:HostedConversation {
                thread_id: $thread_id, request_id: $request_id, status: 'active'
            })
            SET conversation.status = $status, conversation.finished_at = datetime()
            RETURN conversation.thread_id AS thread_id
            """,
            thread_id=thread_id,
            request_id=request_id,
            status=status,
            database_=self.database,
        )

        if not rows:
            raise ConversationError("execution_uncertain")


async def create_conversation_constraints(driver: AsyncDriver, database: str) -> None:
    await driver.execute_query(
        """
        CREATE CONSTRAINT hosted_thread_unique IF NOT EXISTS
        FOR (conversation:HostedConversation) REQUIRE conversation.thread_id IS UNIQUE
        """,
        database_=database,
    )
