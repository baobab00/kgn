"""KGN CLI — query commands: nodes, subgraph, similar."""

from __future__ import annotations

import uuid
from typing import Annotated

import typer
from rich.table import Table

from kgn.cli._app import _project_not_found, console, query_app
from kgn.errors import KgnError

# ── query nodes ────────────────────────────────────────────────────────


@query_app.command("nodes")
def query_nodes(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    node_type: Annotated[
        str | None,
        typer.Option("--type", "-t", help="Node type filter (e.g. SPEC, GOAL)"),
    ] = None,
    node_status: Annotated[
        str | None,
        typer.Option("--status", "-s", help="Node status filter (e.g. ACTIVE)"),
    ] = None,
) -> None:
    """Search nodes — supports type/status filters."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.models.enums import NodeStatus, NodeType

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)

            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            # Parse optional filters
            nt = NodeType(node_type) if node_type else None
            ns = NodeStatus(node_status) if node_status else None

            nodes = repo.search_nodes(project_id, node_type=nt, status=ns)

            if not nodes:
                console.print("\n[dim]No nodes found.[/dim]\n")
                raise typer.Exit(code=0)

            table = Table(title=f"Nodes — {project}", border_style="blue")
            table.add_column("ID (short)", style="dim", width=10)
            table.add_column("Type", style="cyan")
            table.add_column("Status", style="green")
            table.add_column("Title", no_wrap=False)
            table.add_column("Tags", style="yellow")

            for n in nodes:
                short_id = str(n.id)[:8] + ".."
                tags_str = ", ".join(n.tags) if n.tags else ""
                table.add_row(short_id, n.type.value, n.status.value, n.title, tags_str)

            console.print(table)

    except typer.Exit:
        raise
    except KgnError as e:
        console.print(f"\n[bold red][{e.code}] Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


# ── query subgraph ─────────────────────────────────────────────────────


@query_app.command("subgraph")
def query_subgraph(
    node_id: Annotated[str, typer.Argument(help="Root node UUID")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    depth: Annotated[int, typer.Option("--depth", "-d", help="Maximum traversal depth")] = 2,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: table, json, md"),
    ] = "table",
) -> None:
    """Extract subgraph — BFS traversal and context package output."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.graph.subgraph import SubgraphService

    try:
        root_uuid = uuid.UUID(node_id)
    except ValueError:
        console.print(f"\n[bold red]Error:[/bold red] Invalid UUID: {node_id}\n")
        raise typer.Exit(code=1) from None

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)

            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            svc = SubgraphService(repo)
            result = svc.extract(root_uuid, project_id, depth=depth)

            if not result.nodes:
                console.print("\n[dim]No nodes found in subgraph.[/dim]\n")
                raise typer.Exit(code=0)

            if fmt == "json":
                console.print(svc.to_json(result))
            elif fmt == "md":
                console.print(svc.to_markdown(result))
            else:
                _print_subgraph_table(result)

    except typer.Exit:
        raise
    except KgnError as e:
        console.print(f"\n[bold red][{e.code}] Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


# ── query similar ───────────────────────────────────────────────────────


@query_app.command("similar")
def query_similar(
    node_id: Annotated[str, typer.Argument(help="Reference node UUID")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    top: Annotated[int, typer.Option("--top", "-k", help="Maximum number of results")] = 5,
    node_type: Annotated[
        str | None,
        typer.Option("--type", "-t", help="Node type filter (e.g. SPEC, GOAL)"),
    ] = None,
) -> None:
    """Vector similarity search — find nodes most similar to the given node."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.models.enums import NodeType

    try:
        root_uuid = uuid.UUID(node_id)
    except ValueError:
        console.print(f"\n[bold red]Error:[/bold red] Invalid UUID: {node_id}\n")
        raise typer.Exit(code=1) from None

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)

            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            embedding = repo.get_node_embedding(root_uuid)
            if embedding is None:
                console.print(
                    "\n[bold red]Error:[/bold red] Node has no embedding. Run `kgn embed` first.\n"
                )
                raise typer.Exit(code=1)

            nt = NodeType(node_type) if node_type else None

            results = repo.search_similar_nodes(
                embedding,
                project_id,
                top_k=top,
                node_type=nt,
                exclude_ids={root_uuid},
            )

            if not results:
                console.print("\n[dim]No similar nodes found.[/dim]\n")
                raise typer.Exit(code=0)

            _print_similar_table(results, root_uuid)

    except typer.Exit:
        raise
    except KgnError as e:
        console.print(f"\n[bold red][{e.code}] Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


def _print_similar_table(results: list, root_id: uuid.UUID) -> None:
    """Render similar nodes as a Rich table."""
    table = Table(
        title=f"Similar Nodes — query: {str(root_id)[:8]}..",
        border_style="magenta",
    )
    table.add_column("Rank", style="bold", width=5)
    table.add_column("ID (short)", style="dim", width=10)
    table.add_column("Type", style="cyan")
    table.add_column("Title", no_wrap=False)
    table.add_column("Similarity", style="green", justify="right")

    for i, n in enumerate(results, 1):
        short_id = str(n.id)[:8] + ".."
        sim_str = f"{n.similarity:.4f}"
        table.add_row(str(i), short_id, n.type, n.title, sim_str)

    console.print(table)


def _print_subgraph_table(result: SubgraphResult) -> None:  # noqa: F821
    """Render subgraph as a Rich table grouped by depth."""
    table = Table(title=f"Subgraph — root: {result.root_id[:8]}..", border_style="green")
    table.add_column("Depth", style="bold", width=6)
    table.add_column("ID (short)", style="dim", width=10)
    table.add_column("Type", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Title", no_wrap=False)

    for n in sorted(result.nodes, key=lambda x: (x.depth, x.title)):
        short_id = str(n.id)[:8] + ".."
        table.add_row(str(n.depth), short_id, n.type, n.status, n.title)

    console.print(table)

    if result.edges:
        edge_table = Table(title="Edges", border_style="yellow")
        edge_table.add_column("From", style="dim", width=10)
        edge_table.add_column("Type", style="cyan")
        edge_table.add_column("To", style="dim", width=10)
        edge_table.add_column("Note")

        for e in result.edges:
            edge_table.add_row(e.from_id[:8] + "..", e.type, e.to_id[:8] + "..", e.note)

        console.print(edge_table)
