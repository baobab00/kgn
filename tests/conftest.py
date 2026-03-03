"""Shared test fixtures for KGN integration tests.

These fixtures require a running PostgreSQL instance (Docker).
They use a transaction-per-test strategy: each test runs inside a
transaction that is rolled back at teardown, guaranteeing a clean state.
"""

from __future__ import annotations

import uuid

import pytest

# ── pytest hooks ───────────────────────────────────────────────────────


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--run-github`` CLI flag for GitHub E2E tests."""
    parser.addoption(
        "--run-github",
        action="store_true",
        default=False,
        help="Run tests that require a real GitHub PAT (R-032).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``@pytest.mark.github`` tests unless ``--run-github`` is given."""
    if config.getoption("--run-github"):
        return
    skip_github = pytest.mark.skip(reason="need --run-github option to run")
    for item in items:
        if "github" in item.keywords:
            item.add_marker(skip_github)


# ── structlog → stdlib bridge for tests ────────────────────────────────
# Ensure structlog events flow through stdlib logging so that pytest's
# ``caplog`` fixture can capture them.  This runs once at import time;
# ``configure_logging()`` is NOT called during tests to avoid noisy output.
import structlog
from psycopg import Connection

from kgn.db.connection import get_connection
from kgn.db.migrations import run_migrations
from kgn.db.repository import KgnRepository

# Re-export shared helpers so they can be referenced from conftest scope.
from tests.helpers import EMBEDDING_DIMS, MockEmbeddingClient  # noqa: F401

# ── Session-wide pool teardown (R-003) ─────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _close_pool_at_exit():
    """Ensure the connection pool is closed before interpreter shutdown.

    Prevents ``PythonFinalizationError: cannot join thread at interpreter
    shutdown`` warnings caused by ``ConnectionPool.__del__`` running too
    late during garbage collection.
    """
    yield
    from kgn.db.connection import close_pool

    close_pool()


structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.UnicodeDecoder(),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=False,  # allow reconfiguration between tests
)

# ── Environment Safety ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_openai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent accidental real OpenAI API calls during tests.

    Removes ``KGN_OPENAI_API_KEY`` from the environment by default.
    Tests that need the variable should explicitly call
    ``monkeypatch.setenv("KGN_OPENAI_API_KEY", "...")`` which overrides
    this autouse fixture (same ``monkeypatch`` instance).
    """
    monkeypatch.delenv("KGN_OPENAI_API_KEY", raising=False)


# ── DB Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def _migrated_db() -> None:
    """Run migrations once per test session."""
    with get_connection() as conn:
        run_migrations(conn)
        conn.commit()


@pytest.fixture
def db_conn(_migrated_db: None) -> Connection:
    """Provide a connection wrapped in a transaction that rolls back.

    Every test gets a clean snapshot of the DB because we issue a
    SAVEPOINT at the start and ROLLBACK TO it at the end.
    """
    with get_connection() as conn:
        conn.execute("SAVEPOINT test_savepoint")
        yield conn
        conn.execute("ROLLBACK TO SAVEPOINT test_savepoint")


@pytest.fixture
def repo(db_conn: Connection) -> KgnRepository:
    """Repository bound to the test connection."""
    return KgnRepository(db_conn)


@pytest.fixture
def project_id(repo: KgnRepository) -> uuid.UUID:
    """Create a throwaway test project and return its UUID."""
    return repo.get_or_create_project(f"test-project-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def agent_id(repo: KgnRepository, project_id: uuid.UUID) -> uuid.UUID:
    """Create a throwaway test agent and return its UUID."""
    return repo.get_or_create_agent(project_id, f"agent-{uuid.uuid4().hex[:8]}")
