from evals.settings import EvalSettings
from neo4j import AsyncDriver
from typing import cast


async def read_mbappe_birthplace(
    settings: EvalSettings, reader: AsyncDriver
) -> str | None:
    """Check stored scenario facts independently of the agent's claims."""
    async with reader.session(database=settings.neo4j_database) as session:
        result = await session.run(
            "MATCH (player:Player {id: $id}) RETURN player.birthplace AS birthplace",
            id="kylian-mbappe:1998-12-20",
        )
        record = await result.single(strict=True)
        assert record is not None, "Seeded Mbappe player was not found"

        return cast(str | None, record["birthplace"])
