"""KGN CLI — task commands: enqueue, checkout, complete, fail, list, log."""

from __future__ import annotations

import uuid
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kgn.cli._app import _project_not_found, console, task_app

# ── task enqueue ───────────────────────────────────────────────────────


@task_app.command("enqueue")
def task_enqueue(
    node_id: Annotated[str, typer.Argument(help="TASK node UUID")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    priority: Annotated[
        int,
        typer.Option("--priority", help="Priority (lower = higher priority)"),
    ] = 100,
) -> None:
    """Enqueue a TASK node into the task queue."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.graph.subgraph import SubgraphService
    from kgn.task.service import TaskService

    try:
        task_node_uuid = uuid.UUID(node_id)
    except ValueError:
        console.print(f"\n[bold red]Error:[/bold red] Invalid UUID: {node_id}\n")
        raise typer.Exit(code=1) from None

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            svc = TaskService(repo, SubgraphService(repo))
            result = svc.enqueue(project_id, task_node_uuid, priority=priority)
            conn.commit()

        msg = (
            f"\n[green]✅ Task enqueued: {result.task_queue_id} "
            f"(priority: {priority}, state: {result.state})[/green]"
        )
        if result.state == "BLOCKED":
            n = len(result.dependency_check.blocking_tasks)
            msg += f"\n[yellow]⚠ BLOCKED: {n} prerequisite task(s) incomplete[/yellow]"
        console.print(msg + "\n")

    except typer.Exit:
        raise
    except ValueError as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


# ── task checkout ──────────────────────────────────────────────────────


@task_app.command("checkout")
def task_checkout(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    agent: Annotated[str, typer.Option("--agent", "-a", help="Agent key")],
    lease: Annotated[int, typer.Option("--lease", help="Lease duration (seconds)")] = 600,
    fmt: Annotated[
        str | None,
        typer.Option("--format", "-f", help="Output format (json | md)"),
    ] = None,
) -> None:
    """Consume one READY task and print context summary."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.graph.subgraph import SubgraphService
    from kgn.task.formatter import HandoffFormatter
    from kgn.task.service import TaskService

    if fmt is not None and fmt not in ("json", "md"):
        console.print(f"\n[bold red]Error:[/bold red] Unsupported format: {fmt} (json | md)\n")
        raise typer.Exit(code=1)

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            agent_id = repo.get_or_create_agent(project_id, agent)
            svc = TaskService(repo, SubgraphService(repo))

            # Step 6: requeue lease-expired tasks before checkout
            svc.requeue_expired(project_id)

            pkg = svc.checkout(project_id, agent_id, lease_duration_sec=lease)
            conn.commit()

        if pkg is None:
            console.print("\n📭 No tasks available.\n")
            raise typer.Exit(code=0)

        if fmt == "json":
            console.print(HandoffFormatter.to_json(pkg))
        elif fmt == "md":
            console.print(HandoffFormatter.to_markdown(pkg))
        else:
            _print_checkout_panel(pkg)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


def _print_checkout_panel(pkg: ContextPackage) -> None:  # noqa: F821
    """Render checkout result as a Rich Panel."""
    body = Text()
    body.append("Task ID:       ", style="bold")
    body.append(f"{pkg.task.id}\n")
    body.append("Node Title:    ", style="bold")
    body.append(f"{pkg.node.title}\n")
    body.append("Priority:      ", style="bold")
    body.append(f"{pkg.task.priority}\n")
    body.append("Attempt:       ", style="bold")
    body.append(f"{pkg.task.attempts}\n")
    body.append("Lease Expires: ", style="bold")
    body.append(f"{pkg.task.lease_expires_at}\n")
    body.append("Subgraph:      ", style="bold")
    body.append(f"{len(pkg.subgraph.nodes)} nodes, {len(pkg.subgraph.edges)} edges\n")
    body.append("Similar Nodes: ", style="bold")
    body.append(f"{len(pkg.similar_nodes)}\n")

    panel = Panel(body, title="Task Checkout", border_style="cyan")
    console.print(panel)


# ── task complete ──────────────────────────────────────────────────────


@task_app.command("complete")
def task_complete(
    task_id: Annotated[str, typer.Argument(help="Task queue ID")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
) -> None:
    """Mark task as complete (IN_PROGRESS → DONE)."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.graph.subgraph import SubgraphService
    from kgn.task.service import TaskService

    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        console.print(f"\n[bold red]Error:[/bold red] Invalid UUID: {task_id}\n")
        raise typer.Exit(code=1) from None

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            svc = TaskService(repo, SubgraphService(repo))
            result = svc.complete(task_uuid)
            conn.commit()

        msg = f"\n[green]✅ Task {task_id} completed.[/green]"
        if result.unblocked_tasks:
            for ut in result.unblocked_tasks:
                msg += f"\n[cyan]🔓 Unblocked: {ut.node_title} ({ut.task_queue_id})[/cyan]"
        console.print(msg + "\n")

    except typer.Exit:
        raise
    except ValueError as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


# ── task fail ─────────────────────────────────────────────────────────


@task_app.command("fail")
def task_fail(
    task_id: Annotated[str, typer.Argument(help="Task queue ID")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    reason: Annotated[str, typer.Option("--reason", "-r", help="Failure reason")] = "",
) -> None:
    """Mark task as failed (IN_PROGRESS → FAILED)."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.graph.subgraph import SubgraphService
    from kgn.task.service import TaskService

    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        console.print(f"\n[bold red]Error:[/bold red] Invalid UUID: {task_id}\n")
        raise typer.Exit(code=1) from None

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            svc = TaskService(repo, SubgraphService(repo))
            svc.fail(task_uuid, reason=reason)
            conn.commit()

        console.print(f"\n[red]❌ Task {task_id} failed. Reason: {reason}[/red]\n")

    except typer.Exit:
        raise
    except ValueError as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


# ── task list ─────────────────────────────────────────────────────────


@task_app.command("list")
def task_list(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    state: Annotated[
        str | None,
        typer.Option("--state", "-s", help="Status filter (READY, IN_PROGRESS, DONE, FAILED)"),
    ] = None,
) -> None:
    """Print project task list as a table."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            tasks = repo.list_tasks(project_id, state=state)

            if not tasks:
                console.print("\n[dim]No tasks found.[/dim]\n")
                raise typer.Exit(code=0)

            # Resolve node titles
            node_titles: dict[uuid.UUID, str] = {}
            for t in tasks:
                if t.task_node_id not in node_titles:
                    node = repo.get_node_by_id(t.task_node_id)
                    node_titles[t.task_node_id] = node.title if node else "(deleted)"

            _print_task_list_table(tasks, node_titles)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


def _print_task_list_table(
    tasks: list,
    node_titles: dict[uuid.UUID, str],
) -> None:
    """Render task list as a Rich table."""
    table = Table(title="Task Queue", border_style="cyan")
    table.add_column("Task ID", style="dim", width=10)
    table.add_column("Prio", style="bold", justify="right", width=5)
    table.add_column("Node Title", no_wrap=False)
    table.add_column("State", style="cyan")
    table.add_column("Attempt", justify="right", width=8)

    for t in tasks:
        short_id = str(t.id)[:8] + ".."
        title = node_titles.get(t.task_node_id, "—")
        table.add_row(short_id, str(t.priority), title, t.state, str(t.attempts))

    console.print(table)


# ── task log ──────────────────────────────────────────────────────────


@task_app.command("log")
def task_log(
    task_id: Annotated[str, typer.Argument(help="Task queue ID")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
) -> None:
    """Print task activity log."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository

    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        console.print(f"\n[bold red]Error:[/bold red] Invalid UUID: {task_id}\n")
        raise typer.Exit(code=1) from None

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            activities = repo.get_task_activities(task_uuid)

            if not activities:
                console.print("\n[dim]No activities found.[/dim]\n")
                raise typer.Exit(code=0)

            _print_activity_table(task_id, activities)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1) from e
    finally:
        close_pool()


def _print_activity_table(task_id: str, activities: list[dict]) -> None:
    """Render activity log as a Rich table."""
    short_id = task_id[:8] + ".."
    table = Table(title=f"Activity Log — {short_id}", border_style="cyan")
    table.add_column("Timestamp", style="dim", width=20)
    table.add_column("Activity", style="cyan")
    table.add_column("Agent", style="bold", width=15)
    table.add_column("Message", no_wrap=False)

    for a in activities:
        ts = a["created_at"].strftime("%Y-%m-%d %H:%M:%S") if a["created_at"] else "—"
        table.add_row(ts, a["activity_type"], a["agent_key"], a["message"])

    console.print(table)
