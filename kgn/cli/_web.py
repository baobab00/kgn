"""KGN CLI — web command: serve."""

from __future__ import annotations

from typing import Annotated

import typer

from kgn.cli._app import _project_not_found, console, web_app
from kgn.errors import KgnError

# ── web serve ─────────────────────────────────────────────────────────


@web_app.command("serve")
def web_serve(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    host: Annotated[
        str,
        typer.Option("--host", help="Bind host"),
    ] = "localhost",
    port: Annotated[
        int,
        typer.Option("--port", help="Server port"),
    ] = 8080,
) -> None:
    """Launch the KGN web dashboard (read-only).

    Requires the [web] extra: pip install kgn[web]
    """
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        console.print(
            "[red]FastAPI / Uvicorn not installed.[/red]\n"
            "Install with:  [bold]pip install kgn\\[web][/bold]"
        )
        raise typer.Exit(code=1) from None

    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.web.app import create_app

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)
    finally:
        close_pool()

    console.print(
        f"[green]KGN Web Dashboard[/green] — project=[bold]{project}[/bold]  http://{host}:{port}"
    )

    fastapi_app = create_app(project_name=project, project_id=project_id)
    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")
