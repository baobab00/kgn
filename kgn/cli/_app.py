"""KGN CLI — shared application instances, console, and helpers."""

from __future__ import annotations

import os
from typing import Annotated

import typer
from rich.console import Console

from kgn import __version__


def _version_callback(value: bool) -> None:
    if value:
        print(f"kgn {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="kgn",
    help="Parse .kgn/.kge files, ingest into PostgreSQL knowledge graph DB, "
    "query subgraphs, and generate AI agent context packages.",
)


@app.callback(invoke_without_command=True, no_args_is_help=True)
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-V",
            help="Print version and exit",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Enable DEBUG-level logging",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            "-q",
            help="Show WARNING and above only",
        ),
    ] = False,
) -> None:
    """KGN CLI — Knowledge Graph Node management tool."""
    from kgn.logging.config import configure_logging

    if verbose:
        level = "DEBUG"
    elif quiet:
        level = "WARNING"
    else:
        level = os.environ.get("KGN_LOG_LEVEL", "INFO")

    configure_logging(
        level=level,
        fmt=os.environ.get("KGN_LOG_FORMAT", "console"),
    )


query_app = typer.Typer(help="Query nodes and extract subgraphs")
app.add_typer(query_app, name="query")

conflict_app = typer.Typer(help="Conflict detection and management")
app.add_typer(conflict_app, name="conflict")

task_app = typer.Typer(help="Task queue management")
app.add_typer(task_app, name="task")

mcp_app = typer.Typer(help="MCP server management")
app.add_typer(mcp_app, name="mcp")

embed_app = typer.Typer(help="Embedding management")
app.add_typer(embed_app, name="embed")

sync_app = typer.Typer(help="DB ↔ File system synchronization")
app.add_typer(sync_app, name="sync")

git_app = typer.Typer(help="Git repository management")
app.add_typer(git_app, name="git")

branch_app = typer.Typer(help="Branch management")
git_app.add_typer(branch_app, name="branch")

pr_app = typer.Typer(help="Pull request management")
git_app.add_typer(pr_app, name="pr")

graph_app = typer.Typer(help="Graph visualization")
app.add_typer(graph_app, name="graph")

web_app = typer.Typer(help="Web dashboard")
app.add_typer(web_app, name="web")

agent_app = typer.Typer(help="Agent role management")
app.add_typer(agent_app, name="agent")

workflow_app = typer.Typer(help="Workflow management")
app.add_typer(workflow_app, name="workflow")

lsp_app = typer.Typer(help="Language Server Protocol")
app.add_typer(lsp_app, name="lsp")

console = Console()


def _project_not_found(name: str) -> None:
    """Print error and raise Exit for missing project."""
    console.print(f"\n[bold red]Error:[/bold red] Project '{name}' not found\n")
    raise typer.Exit(code=1)
