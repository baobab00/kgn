"""KGN CLI — agent commands: list, role, stats, timeline."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from kgn.cli._app import _project_not_found, agent_app, console
from kgn.errors import KgnError

# ── agent list ────────────────────────────────────────────────────────


@agent_app.command("list")
def agent_list(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
) -> None:
    """List all agents in the project."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_project_by_name(project)
            if not pid:
                _project_not_found(project)

            agents = repo.list_agents(pid)  # type: ignore[arg-type]
            if not agents:
                console.print("[dim]No agents found.[/dim]")
                return

            table = Table(title=f"Agents — {project}")
            table.add_column("Key", style="cyan")
            table.add_column("Role", style="green")
            table.add_column("ID", style="dim")
            table.add_column("Created", style="dim")

            for ag in agents:
                table.add_row(
                    ag["agent_key"],
                    str(ag["role"]),
                    str(ag["id"])[:8],
                    str(ag.get("created_at", ""))[:19],
                )
            console.print(table)
    except KgnError as e:
        console.print(f"[bold red][{e.code}] Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


# ── agent role ────────────────────────────────────────────────────────


@agent_app.command("role")
def agent_role_set(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    agent: Annotated[str, typer.Option("--agent", "-a", help="Agent key")],
    role: Annotated[str, typer.Argument(help="Role (genesis, worker, reviewer, indexer, admin)")],
) -> None:
    """Set agent role."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.models.enums import AgentRole

    valid_roles = {r.value for r in AgentRole}
    if role not in valid_roles:
        console.print(
            f"[bold red]Error:[/bold red] Invalid role '{role}'. Valid: {sorted(valid_roles)}"
        )
        raise typer.Exit(code=1)

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_project_by_name(project)
            if not pid:
                _project_not_found(project)

            agent_rec = repo.get_agent_by_key(pid, agent)  # type: ignore[arg-type]
            if not agent_rec:
                console.print(
                    f"[bold red]Error:[/bold red] Agent '{agent}' not found in project '{project}'"
                )
                raise typer.Exit(code=1)

            repo.set_agent_role(agent_rec["id"], role)
            conn.commit()
            console.print(f"[green]✅ Agent '{agent}' role set to '{role}'[/green]")
    except typer.Exit:
        raise
    except KgnError as e:
        console.print(f"[bold red][{e.code}] Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


# ── agent stats ───────────────────────────────────────────────────────


@agent_app.command("stats")
def agent_stats(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
) -> None:
    """Print per-agent task statistics (completed/failed/avg time)."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.orchestration.observability import ObservabilityService

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_project_by_name(project)
            if not pid:
                _project_not_found(project)

            svc = ObservabilityService(repo)
            stats = svc.get_agent_stats(pid)  # type: ignore[arg-type]

            if not stats:
                console.print("[dim]No agents found.[/dim]")
                return

            table = Table(title=f"Agent Stats — {project}")
            table.add_column("Agent", style="cyan")
            table.add_column("Role", style="green")
            table.add_column("Done", justify="right")
            table.add_column("Failed", justify="right", style="red")
            table.add_column("Total", justify="right")
            table.add_column("Rate", justify="right")
            table.add_column("Avg Time", justify="right", style="dim")

            for s in stats:
                rate = f"{s.success_rate:.1f}%"
                avg_time = f"{s.avg_duration_sec:.1f}s" if s.avg_duration_sec > 0 else "-"
                table.add_row(
                    s.agent_key,
                    s.role,
                    str(s.done_count),
                    str(s.failed_count),
                    str(s.total_tasks),
                    rate,
                    avg_time,
                )
            console.print(table)
    except typer.Exit:
        raise
    except KgnError as e:
        console.print(f"[bold red][{e.code}] Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


# ── agent timeline ────────────────────────────────────────────────────


@agent_app.command("timeline")
def agent_timeline(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    agent: Annotated[
        str | None,
        typer.Option("--agent", "-a", help="Agent key (omit for all)"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Maximum output count")] = 20,
) -> None:
    """Print agent activity timeline."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.orchestration.observability import ObservabilityService

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_project_by_name(project)
            if not pid:
                _project_not_found(project)

            agent_id = None
            if agent:
                agent_rec = repo.get_agent_by_key(pid, agent)  # type: ignore[arg-type]
                if not agent_rec:
                    console.print(f"[bold red]Error:[/bold red] Agent '{agent}' not found")
                    raise typer.Exit(code=1)
                agent_id = agent_rec["id"]

            svc = ObservabilityService(repo)
            entries = svc.get_agent_timeline(pid, agent_id, limit=limit)  # type: ignore[arg-type]

            if not entries:
                console.print("[dim]No activities found.[/dim]")
                return

            title = f"Timeline — {agent or 'all agents'} ({project})"
            table = Table(title=title)
            table.add_column("Time", style="dim", width=19)
            table.add_column("Agent", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Message", no_wrap=False)

            for e in entries:
                time_str = str(e.created_at)[:19] if e.created_at else ""
                table.add_row(
                    time_str,
                    e.agent_key,
                    e.activity_type,
                    e.message[:80] if e.message else "",
                )
            console.print(table)
    except typer.Exit:
        raise
    except KgnError as e:
        console.print(f"[bold red][{e.code}] Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()
