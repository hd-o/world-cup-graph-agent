import langsmith
import os
import pytest
import pytest_asyncio
import structlog
import subprocess
from collections.abc import AsyncIterator, Iterator
from evals.database import prepare_eval_database, reset_eval_database
from evals.reporting.costs import attach_openrouter_costs
from evals.reporting.metadata import build_eval_metadata
from evals.runtime import (
    ConversationRunner,
    build_eval_config,
    invoke_conversation,
)
from evals.settings import EvalSettings
from functools import partial
from langchain_core.runnables import RunnableConfig
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from pathlib import Path
from world_cup_graph_agent.infrastructure.neo4j.checkpoints import CheckpointSaver
from world_cup_graph_agent.infrastructure.neo4j.driver import (
    GraphDrivers,
    create_graph_drivers,
)


logger = structlog.get_logger(__name__)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--hosted",
        action="store_true",
        help="Authorize public cloud access/research-rejection evals",
    )
    parser.addoption(
        "--hosted-cloud",
        choices=("aws", "gcp"),
        help="Select one provider for authorized hosted evals",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    # Public hosted calls are deliberately opt-in, even when cloud credentials
    # happen to be available in the operator's development shell.
    cloud = config.getoption("hosted_cloud")
    excluded = [
        case
        for case in items
        if (case.get_closest_marker("hosted") and not config.getoption("hosted"))
        or (
            case.get_closest_marker("hosted")
            and cloud
            and getattr(case, "callspec", None)
            and case.callspec.params.get("cloud") != cloud
        )
    ]
    if excluded:
        items[:] = [case for case in items if case not in excluded]
        config.hook.pytest_deselected(items=excluded)


@pytest.fixture(name="development_server", scope="session", autouse=True)
def start_development_server(request: pytest.FixtureRequest) -> None:
    if all(case.get_closest_marker("hosted") for case in request.session.items):
        return

    # Poe forwards these flags to Compose, whose health checks gate
    # readiness even though the development stack runs detached.
    logger.info("eval_development_server_startup_started")

    subprocess.run(
        ["uv", "run", "poe", "dev", "--wait", "--wait-timeout", "120"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        timeout=600,
    )

    logger.info("eval_development_server_startup_completed")


@pytest.fixture(name="settings", scope="session")
def load_settings() -> EvalSettings:
    return EvalSettings()


@pytest.fixture(scope="session", autouse=True)
def configure_langsmith_reporting(request: pytest.FixtureRequest) -> Iterator[None]:
    # Ordinary access and credential checks do not need eval reporting settings.
    if not any(case.get_closest_marker("langsmith") for case in request.session.items):
        yield
        return

    settings: EvalSettings = request.getfixturevalue("settings")
    tracking = os.getenv("LANGSMITH_TEST_TRACKING", "true") != "false"
    client = None

    if tracking or settings.langsmith_tracing:
        client = langsmith.Client(
            api_key=settings.langsmith_api_key.get_secret_value(),
            process_buffered_run_ops=attach_openrouter_costs,
            run_ops_buffer_size=1,
            hide_inputs=lambda inputs: {
                name: value
                for name, value in inputs.items()
                if name
                not in {
                    "conversation_runner",
                    "config",
                    "settings",
                    "drivers",
                    "checkpointer",
                    "run_collector",
                    "judge",
                }
            },
        )

    langsmith.configure(
        client=client, enabled=None, project_name=settings.langsmith_project
    )

    try:
        yield
    finally:
        if client is not None:
            logger.info("eval_langsmith_flush_started")
            client.flush()
            logger.info("eval_langsmith_flush_completed")
        langsmith.configure(client=None, enabled=None, project_name=None)


@pytest_asyncio.fixture(autouse=True)
async def configure_scenario_tracing(
    request: pytest.FixtureRequest,
) -> AsyncIterator[None]:
    # Credential checks invoke a real LangChain tool with a stubbed model.
    # Disable its automatic callbacks even when marked cases share this run.
    langsmith_marker = request.node.get_closest_marker("langsmith")
    settings = request.getfixturevalue("settings") if langsmith_marker else None

    with langsmith.tracing_context(
        enabled=settings.langsmith_tracing if settings else False
    ):
        yield


@pytest.fixture(name="langsmith_experiment_metadata", scope="session")
def build_experiment_metadata(settings: EvalSettings) -> dict:
    return build_eval_metadata(settings)


@pytest_asyncio.fixture(name="drivers", scope="session")
async def open_drivers(settings: EvalSettings) -> AsyncIterator[GraphDrivers]:
    try:
        logger.info("eval_database_bootstrap_started")
        await prepare_eval_database(settings)
        logger.info("eval_database_bootstrap_completed")
    except Exception as error:
        pytest.exit(f"Eval database bootstrap failed: {error}", returncode=1)

    drivers = create_graph_drivers(settings)

    try:
        yield drivers
    finally:
        logger.info("eval_database_close_started")
        await drivers.close()
        logger.info("eval_database_close_completed")


@pytest_asyncio.fixture(name="conversation_runner")
async def create_conversation_runner(
    settings: EvalSettings, drivers: GraphDrivers
) -> ConversationRunner:
    try:
        await reset_eval_database(settings, drivers.writer)
    except Exception as error:
        pytest.exit(f"Eval database reset failed: {error}", returncode=1)

    checkpointer = CheckpointSaver(
        drivers.writer, database=settings.neo4j_checkpoint_database
    )

    return partial(invoke_conversation, settings, drivers, checkpointer)


@pytest.fixture(name="config")
def build_scenario_config(
    settings: EvalSettings,
    run_collector: RunCollectorCallbackHandler,
    request: pytest.FixtureRequest,
) -> RunnableConfig:
    family = "grounded" if request.node.get_closest_marker("grounded") else "enrichment"
    scenario = request.node.name.removeprefix("test_")
    logger.info("eval_scenario_started", family=family, scenario=scenario)
    config = build_eval_config(settings, family, scenario)
    config["callbacks"] = [run_collector]
    return config


@pytest.fixture(name="run_collector")
def create_run_collector() -> RunCollectorCallbackHandler:
    # Subagent tool activity is absent from the supervisor's message history.
    # Capture it locally even when LangSmith uploads are disabled.
    run_collector = RunCollectorCallbackHandler()
    run_collector.raise_error = True
    return run_collector
