from pydantic import Field, SecretStr
from typing import ClassVar
from world_cup_graph_agent.config.settings import ProviderSort, Settings


class EvalSettings(Settings):
    # Pydantic excludes ClassVar from settings input, preventing environment
    # and constructor values from redirecting destructive eval setup.
    # The type checker rejects this supported instance-field conversion:
    # https://docs.pydantic.dev/latest/concepts/models/#class-variables
    neo4j_uri: ClassVar[str] = "bolt://localhost:7688"  # type: ignore[bad-override]

    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str | None = None
    langsmith_tracing: bool = True

    # Eval administration uses the credential assigned to the isolated service.
    neo4j_bootstrap_password: SecretStr = Field(
        default=SecretStr("worldcup_eval_bootstrap_password"),
        validation_alias="NEO4J_EVAL_BOOTSTRAP_PASSWORD",
    )

    openrouter_judge_model_id: str = Field(default="", min_length=1)
    openrouter_judge_providers: list[str] = Field(default_factory=list)
    openrouter_judge_sort: ProviderSort = "throughput"
    openrouter_judge_reasoning_effort: str = Field(default="", min_length=1)
