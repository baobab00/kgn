"""KGN CLI — workflow commands: list, run."""

from __future__ import annotations

import contextlib
from typing import Annotated

import typer
from rich.table import Table

from kgn.cli._app import _project_not_found, console, workflow_app
from kgn.errors import KgnError

# ── workflow list ─────────────────────────────────────────────────────


@workflow_app.command("list")
def workflow_list() -> None:
    """List registered workflow templates."""
    from kgn.orchestration.templates import BUILTIN_TEMPLATES

    table = Table(title="Workflow Templates")
    table.add_column("Name", style="cyan")
    table.add_column("Display Name")
    table.add_column("Trigger")
    table.add_column("Steps", justify="right")

    for tmpl in BUILTIN_TEMPLATES:
        table.add_row(
            tmpl.name,
            tmpl.display_name,
            f"{tmpl.trigger_node_type} ({tmpl.trigger_status})",
            str(len(tmpl.steps)),
        )

    console.print(table)


# ── workflow run ──────────────────────────────────────────────────────


@workflow_app.command("run")
def workflow_run(
    template: Annotated[str, typer.Argument(help="Workflow template name")],
    node_id: Annotated[str, typer.Argument(help="Trigger node ID (UUID)")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    agent: Annotated[str, typer.Option("--agent", "-a", help="Agent name")] = "cli",
    priority: Annotated[int, typer.Option("--priority", help="Task priority")] = 100,
) -> None:
    """Execute a workflow template to create a subtask DAG."""
    import uuid as _uuid

    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.embedding.client import EmbeddingClient
    from kgn.graph.subgraph import SubgraphService
    from kgn.orchestration.templates import register_builtins
    from kgn.orchestration.workflow import WorkflowEngine
    from kgn.task.service import TaskService

    try:
        trigger_uuid = _uuid.UUID(node_id)
    except ValueError:
        console.print(f"[bold red]Error:[/bold red] Invalid UUID: {node_id}")
        raise typer.Exit(code=1) from None

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)
            pid = repo.get_project_by_name(project)
            if not pid:
                _project_not_found(project)

            agent_id = repo.get_or_create_agent(pid, agent)  # type: ignore[arg-type]

            subgraph_svc = SubgraphService(repo)
            embedding_client: EmbeddingClient | None = None
            with contextlib.suppress(Exception):
                embedding_client = EmbeddingClient()
            task_svc = TaskService(repo, subgraph_svc, embedding_client)

            engine = WorkflowEngine(repo, task_svc)
            register_builtins(engine)

            result = engine.execute(
                trigger_node_id=trigger_uuid,
                project_id=pid,  # type: ignore[arg-type]
                agent_id=agent_id,
                template_name=template,
                priority=priority,
            )
            conn.commit()

            # Display results
            table = Table(title=f"Workflow '{template}' execution results")
            table.add_column("Step", style="cyan")
            table.add_column("Node Type")
            table.add_column("Title")
            table.add_column("Node ID", style="dim")
            table.add_column("Queue State")

            for cn in result.created_nodes:
                state = cn.enqueue_result.state if cn.enqueue_result else "—"
                table.add_row(
                    cn.step_id,
                    str(cn.node_type),
                    cn.title,
                    str(cn.node_id)[:8] + "…",
                    state,
                )

            console.print(table)
            console.print(
                f"\n[green]✅ {result.node_count} nodes created, "
                f"{result.task_count} tasks enqueued[/green]"
            )
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
