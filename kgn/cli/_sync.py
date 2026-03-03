"""KGN CLI — sync commands: export, import, status, push, pull."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from kgn.cli._app import _project_not_found, console, sync_app

# ── sync export ────────────────────────────────────────────────────────


@sync_app.command("export")
def sync_export(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="Sync root directory"),
    ] = Path("./kgn-data"),
    agent: Annotated[
        str,
        typer.Option("--agent", "-a", help="Agent key (default: cli-agent)"),
    ] = "cli-agent",
    commit: Annotated[
        bool,
        typer.Option("--commit", help="Auto git commit after export"),
    ] = False,
) -> None:
    """Export DB → file system (.kgn/.kge files)."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.sync.export_service import ExportService

    target_dir = target.resolve()

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            service = ExportService(repo)
            result = service.export_project(
                project_name=project,
                project_id=project_id,
                target_dir=target_dir,
                agent_id=agent,
            )
            conn.commit()

            # Auto-generate README.md with Mermaid diagrams (R-029: non-fatal)
            from kgn.graph.mermaid import MermaidGenerator

            try:
                mermaid_gen = MermaidGenerator(repo)
                readme_path = mermaid_gen.generate_readme(
                    project_id=project_id,
                    project_name=project,
                    target_dir=target_dir,
                )
            except Exception:  # noqa: BLE001
                import structlog as _slog

                _slog.get_logger("kgn.cli").warning(
                    "readme_generation_failed",
                    project=project,
                )
                readme_path = None

        console.print(f"\n[bold]KGN Sync Export — {project}[/bold]")
        console.print(f"  Target: {target_dir}")

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_row("[green]Exported[/green]", str(result.exported))
        table.add_row("[dim]Skipped[/dim]", str(result.skipped))
        table.add_row("[yellow]Deleted[/yellow]", str(result.deleted))
        if result.errors:
            table.add_row("[red]Errors[/red]", str(result.error_count))
        console.print(table)

        if result.errors:
            for err in result.errors:
                console.print(f"  [red]•[/red] {err}")
            raise typer.Exit(code=1)

        console.print("\n[bold green]Export complete.[/bold green]")
        console.print(f"  [dim]README: {readme_path}[/dim]")

        # Auto-commit if --commit flag is set
        if commit:
            from kgn.git.service import GitService

            git_svc = GitService(target_dir)
            n_edges = result.total - result.exported
            msg = f"kgn: export {project} ({result.exported} nodes, {n_edges} edges)"
            git_result = git_svc.commit(msg)
            if "Nothing to commit" in git_result.message:
                console.print("[dim]  Git: nothing to commit[/dim]")
            else:
                console.print(f"[green]  Git: committed — {msg}[/green]")

        console.print()

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    finally:
        close_pool()


# ── sync import ────────────────────────────────────────────────────────


@sync_app.command("import")
def sync_import(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    source: Annotated[
        Path,
        typer.Option("--source", "-s", help="Sync root directory"),
    ] = Path("./kgn-data"),
    agent: Annotated[
        str,
        typer.Option("--agent", "-a", help="Agent key (default: cli-agent)"),
    ] = "cli-agent",
) -> None:
    """Import file system → DB (.kgn/.kge files)."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.sync.import_service import ImportService

    source_dir = source.resolve()

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_or_create_project(project)
            agent_id = repo.get_or_create_agent(
                project_id=project_id,
                agent_key=agent,
            )

            service = ImportService(repo)
            result = service.import_project(
                project_name=project,
                project_id=project_id,
                agent_id=agent_id,
                source_dir=source_dir,
            )
            conn.commit()

        console.print(f"\n[bold]KGN Sync Import — {project}[/bold]")
        console.print(f"  Source: {source_dir}")

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_row("[green]Imported[/green]", str(result.imported))
        table.add_row("[dim]Skipped[/dim]", str(result.skipped))
        if result.failed:
            table.add_row("[red]Failed[/red]", str(result.failed))
        console.print(table)

        if result.errors:
            for err in result.errors:
                console.print(f"  [red]•[/red] {err}")
            raise typer.Exit(code=1)

        console.print("\n[bold green]Import complete.[/bold green]\n")

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    finally:
        close_pool()


# ── sync status ────────────────────────────────────────────────────────


@sync_app.command("status")
def sync_status(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="Sync root directory"),
    ] = Path("./kgn-data"),
) -> None:
    """Compare DB and file system sync status."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.sync.import_service import get_sync_status

    target_dir = target.resolve()

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            status = get_sync_status(repo, project, project_id, target_dir)

        console.print(f"\n[bold]KGN Sync Status — {project}[/bold]")
        console.print(f"  Target: {target_dir}\n")

        table = Table(show_header=True, padding=(0, 2))
        table.add_column("", style="bold")
        table.add_column("DB", justify="right")
        table.add_column("Files", justify="right")
        table.add_column("Diff", justify="right")

        n_diff = status.node_diff
        n_style = "green" if n_diff == 0 else "yellow"
        n_text = "0" if n_diff == 0 else f"+{n_diff}" if n_diff > 0 else str(n_diff)

        e_diff = status.edge_diff
        e_style = "green" if e_diff == 0 else "yellow"
        e_text = "0" if e_diff == 0 else f"+{e_diff}" if e_diff > 0 else str(e_diff)

        table.add_row(
            "Nodes",
            str(status.db_node_count),
            str(status.file_node_count),
            f"[{n_style}]{n_text}[/{n_style}]",
        )
        table.add_row(
            "Edges",
            str(status.db_edge_count),
            str(status.file_edge_count),
            f"[{e_style}]{e_text}[/{e_style}]",
        )
        console.print(table)

        if status.last_export:
            console.print(f"\n  Last export: {status.last_export}")
        if status.last_import:
            console.print(f"  Last import: {status.last_import}")
        console.print()

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    finally:
        close_pool()


# ── sync push ─────────────────────────────────────────────────────────


@sync_app.command("push")
def sync_push(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="Sync root directory"),
    ] = Path("./kgn-data"),
    agent: Annotated[
        str,
        typer.Option("--agent", "-a", help="Agent key (default: cli-agent)"),
    ] = "cli-agent",
    message: Annotated[
        str | None,
        typer.Option("--message", "-m", help="Commit message (omit for auto-generation)"),
    ] = None,
) -> None:
    """DB → export → commit → push pipeline."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.git.service import GitService
    from kgn.github.sync_service import SyncService

    target_dir = target.resolve()

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            git_svc = GitService(target_dir)
            sync_svc = SyncService(git_service=git_svc)

            result = sync_svc.push(
                project_name=project,
                project_id=project_id,
                sync_dir=target_dir,
                repo=repo,
                message=message,
                agent_id=agent,
            )
            conn.commit()

        console.print(f"\n[bold]KGN Sync Push — {project}[/bold]")
        if result.exported:
            console.print(f"  Exported: {result.exported}")
        if result.committed:
            console.print("  [green]Committed[/green]")
        if result.pushed:
            console.print("  [green]Pushed to remote[/green]")
        console.print(f"  {result.message}")
        console.print()

        if not result.success:
            raise typer.Exit(code=1)

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    finally:
        close_pool()


# ── sync pull ─────────────────────────────────────────────────────────


@sync_app.command("pull")
def sync_pull(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="Sync root directory"),
    ] = Path("./kgn-data"),
    agent: Annotated[
        str,
        typer.Option("--agent", "-a", help="Agent key (default: cli-agent)"),
    ] = "cli-agent",
    conflict_strategy: Annotated[
        str,
        typer.Option("--strategy", help="Conflict resolution strategy (db-wins|file-wins|manual)"),
    ] = "db-wins",
) -> None:
    """pull → import → DB pipeline."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.git.service import GitService
    from kgn.github.sync_service import ConflictStrategy, SyncService

    target_dir = target.resolve()

    try:
        strategy = ConflictStrategy(conflict_strategy)
    except ValueError:
        console.print(
            f"[red]Invalid strategy: {conflict_strategy}[/red]\nUse: db-wins, file-wins, manual"
        )
        raise typer.Exit(code=1) from None

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_or_create_project(project)
            agent_id = repo.get_or_create_agent(
                project_id=project_id,
                agent_key=agent,
            )

            git_svc = GitService(target_dir)
            sync_svc = SyncService(
                git_service=git_svc,
                conflict_strategy=strategy,
            )

            result = sync_svc.pull(
                project_name=project,
                project_id=project_id,
                agent_id=agent_id,
                sync_dir=target_dir,
                repo=repo,
            )
            conn.commit()

        console.print(f"\n[bold]KGN Sync Pull — {project}[/bold]")
        if result.imported:
            console.print(f"  Imported: {result.imported}")
        if result.has_conflicts:
            console.print(f"  [yellow]Conflicts: {len(result.conflicts)}[/yellow]")
            for c in result.conflicts:
                console.print(f"    [yellow]•[/yellow] {c.file_path} ({c.reason})")
        console.print(f"  {result.message}")
        console.print()

        if not result.success:
            raise typer.Exit(code=1)

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    finally:
        close_pool()
