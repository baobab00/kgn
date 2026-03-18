"""KGN CLI — conflict commands: scan, approve, dismiss."""

from __future__ import annotations

import uuid
from typing import Annotated

import typer
from rich.table import Table

from kgn.cli._app import _project_not_found, conflict_app, console
from kgn.errors import KgnError

# ── conflict scan ──────────────────────────────────────────────────────


@conflict_app.command("scan")
def conflict_scan(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    threshold: Annotated[
        float,
        typer.Option("--threshold", "-t", help="Similarity threshold"),
    ] = 0.92,
) -> None:
    """Scan conflict candidates — list node pairs exceeding similarity threshold."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)

            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            from kgn.conflict.service import ConflictService

            svc = ConflictService(repo)
            candidates = svc.scan(project_id, threshold=threshold)

            if not candidates:
                console.print("\n[dim]No conflict candidates found.[/dim]\n")
                raise typer.Exit(code=0)

            table = Table(title=f"Conflict Candidates — {project}", border_style="red")
            table.add_column("#", style="bold", width=4)
            table.add_column("Node A", no_wrap=False)
            table.add_column("Node B", no_wrap=False)
            table.add_column("Similarity", style="green", justify="right")
            table.add_column("Status", style="yellow")

            for i, c in enumerate(candidates, 1):
                a_label = f"{str(c.node_a_id)[:8]}.. {c.node_a_title}"
                b_label = f"{str(c.node_b_id)[:8]}.. {c.node_b_title}"
                table.add_row(str(i), a_label, b_label, f"{c.similarity:.4f}", c.status)

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


# ── conflict approve ───────────────────────────────────────────────────


@conflict_app.command("approve")
def conflict_approve(
    node_a: Annotated[str, typer.Argument(help="Node A UUID")],
    node_b: Annotated[str, typer.Argument(help="Node B UUID")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    note: Annotated[str, typer.Option("--note", "-n", help="Memo")] = "",
) -> None:
    """Approve conflict — create/update CONTRADICTS edge to APPROVED."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository

    try:
        uuid_a = uuid.UUID(node_a)
        uuid_b = uuid.UUID(node_b)
    except ValueError as exc:
        console.print(f"\n[bold red]Error:[/bold red] Invalid UUID: {exc}\n")
        raise typer.Exit(code=1) from None

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)

            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            from kgn.conflict.service import ConflictService

            svc = ConflictService(repo)
            edge_id = svc.approve(project_id, uuid_a, uuid_b, note=note)
            conn.commit()

        console.print(f"\n[green]✅ CONTRADICTS edge approved (id: {edge_id})[/green]\n")

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


# ── conflict dismiss ───────────────────────────────────────────────────


@conflict_app.command("dismiss")
def conflict_dismiss(
    node_a: Annotated[str, typer.Argument(help="Node A UUID")],
    node_b: Annotated[str, typer.Argument(help="Node B UUID")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    note: Annotated[str, typer.Option("--note", "-n", help="Memo")] = "",
) -> None:
    """Dismiss conflict — create/update CONTRADICTS edge to DISMISSED."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository

    try:
        uuid_a = uuid.UUID(node_a)
        uuid_b = uuid.UUID(node_b)
    except ValueError as exc:
        console.print(f"\n[bold red]Error:[/bold red] Invalid UUID: {exc}\n")
        raise typer.Exit(code=1) from None

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)

            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            from kgn.conflict.service import ConflictService

            svc = ConflictService(repo)
            edge_id = svc.dismiss(project_id, uuid_a, uuid_b, note=note)
            conn.commit()

        console.print(f"\n[yellow]⏭️  CONTRADICTS edge dismissed (id: {edge_id})[/yellow]\n")

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
