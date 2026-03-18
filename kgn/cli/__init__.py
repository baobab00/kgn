"""KGN CLI package.

Entry point: `kgn = "kgn.cli:app"` (pyproject.toml).

Each submodule registers its commands with the relevant Typer app via
decorator side effects on import. Importing this package is enough to
make all commands available.
"""

# Import command modules so their decorators register commands with the apps.
from kgn.cli import (  # noqa: F401
    _agent,
    _conflict,
    _core,
    _embed,
    _git,
    _graph,
    _lsp,
    _mcp,
    _query,
    _sync,
    _task,
    _web,
    _workflow,
)
from kgn.cli._app import app  # noqa: F401 — re-exported entry point
from kgn.errors import KgnError

__all__ = ["app"]
