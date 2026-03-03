"""KGN CLI — graph commands: mermaid, readme."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

import typer

from kgn.cli._app import _project_not_found, console, graph_app

# ── graph mermaid ─────────────────────────────────────────────────────


@graph_app.command("mermaid")
def graph_mermaid(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    root: Annotated[
        str | None,
        typer.Option("--root", "-r", help="Center node ID (subgraph)"),
    ] = None,
    depth: Annotated[
        int,
        typer.Option("--depth", "-d", help="Subgraph depth"),
    ] = 3,
    task_board: Annotated[
        bool,
        typer.Option("--task-board", help="Task board mode"),
    ] = False,
    no_status: Annotated[
        bool,
        typer.Option("--no-status", help="Hide node statuses"),
    ] = False,
) -> None:
    """Generate Mermaid diagram (flowchart / task-board)."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.graph.mermaid import MermaidGenerator

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            gen = MermaidGenerator(repo)

            if task_board:
                result = gen.generate_task_board(project_id)
            else:
                root_id = uuid.UUID(root) if root else None
                result = gen.generate_graph(
                    project_id,
                    root_node_id=root_id,
                    depth=depth,
                    include_status=not no_status,
                )

        console.print("\n```mermaid")
        console.print(result.diagram)
        console.print("```\n")
        console.print(f"[dim]Nodes: {result.node_count} | Edges: {result.edge_count}[/dim]\n")

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    finally:
        close_pool()


# ── graph readme ──────────────────────────────────────────────────────


@graph_app.command("readme")
def graph_readme(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="README.md output directory"),
    ] = Path("./kgn-data"),
) -> None:
    """Auto-generate README.md (with Mermaid diagram)."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.graph.mermaid import MermaidGenerator

    target_dir = target.resolve()

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            gen = MermaidGenerator(repo)
            readme_path = gen.generate_readme(
                project_id=project_id,
                project_name=project,
                target_dir=target_dir,
            )

        console.print(f"\n[bold green]README generated:[/bold green] {readme_path}\n")

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    finally:
        close_pool()
