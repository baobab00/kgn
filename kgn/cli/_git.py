"""KGN CLI — git commands: init, status, diff, log, branch, pr."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from kgn.cli._app import branch_app, console, git_app, pr_app
from kgn.errors import KgnError

# ── git init ──────────────────────────────────────────────────────────


@git_app.command("init")
def git_init(
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="Sync root directory"),
    ] = Path("./kgn-data"),
) -> None:
    """Initialize a Git repository in the directory and create .gitignore."""
    from kgn.git.service import GitService

    try:
        svc = GitService(target.resolve())
        result = svc.init()
        console.print(f"\n[green]✅ Git repository initialized: {target.resolve()}[/green]")
        if result.message:
            console.print(f"  {result.message}")
        console.print()
    except KgnError as exc:
        console.print(f"\n[bold red][{exc.code}] Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc


# ── git status ────────────────────────────────────────────────────────


@git_app.command("status")
def git_status(
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="Sync root directory"),
    ] = Path("./kgn-data"),
) -> None:
    """Check Git repository status."""
    from kgn.git.service import GitService

    try:
        svc = GitService(target.resolve())
        status = svc.status()

        if status.is_clean:
            console.print("\n[green]Working tree clean — no changes[/green]\n")
            return

        console.print("\n[bold]Git Status[/bold]")
        if status.added:
            for f in status.added:
                console.print(f"  [green]A[/green]  {f}")
        if status.modified:
            for f in status.modified:
                console.print(f"  [yellow]M[/yellow]  {f}")
        if status.deleted:
            for f in status.deleted:
                console.print(f"  [red]D[/red]  {f}")
        if status.untracked:
            for f in status.untracked:
                console.print(f"  [dim]?[/dim]  {f}")
        console.print(f"\n  Total changes: {status.total_changes}\n")

    except KgnError as exc:
        console.print(f"\n[bold red][{exc.code}] Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc


# ── git diff ──────────────────────────────────────────────────────────


@git_app.command("diff")
def git_diff(
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="Sync root directory"),
    ] = Path("./kgn-data"),
    cached: Annotated[
        bool,
        typer.Option("--cached", help="Show only staged changes"),
    ] = False,
) -> None:
    """Show Git diff."""
    from kgn.git.service import GitService

    try:
        svc = GitService(target.resolve())
        diff_output = svc.diff(cached=cached)

        if not diff_output:
            console.print("\n[dim]No differences.[/dim]\n")
        else:
            console.print(diff_output)

    except KgnError as exc:
        console.print(f"\n[bold red][{exc.code}] Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc


# ── git log ───────────────────────────────────────────────────────────


@git_app.command("log")
def git_log(
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="Sync root directory"),
    ] = Path("./kgn-data"),
    count: Annotated[
        int,
        typer.Option("--count", "-n", help="Number of commits to display"),
    ] = 10,
) -> None:
    """Show recent commit history."""
    from kgn.git.service import GitService

    try:
        svc = GitService(target.resolve())
        entries = svc.log(n=count)

        if not entries:
            console.print("\n[dim]No commits yet.[/dim]\n")
            return

        console.print(f"\n[bold]Git Log (last {len(entries)} commits)[/bold]\n")
        for entry in entries:
            console.print(
                f"  [yellow]{entry.short_hash}[/yellow]  "
                f"{entry.subject}  "
                f"[dim]({entry.author}, {entry.date})[/dim]"
            )
        console.print()

    except KgnError as exc:
        console.print(f"\n[bold red][{exc.code}] Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc


# ── branch list ───────────────────────────────────────────────────────


@branch_app.command("list")
def branch_list(
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="Sync root directory"),
    ] = Path("./kgn-data"),
) -> None:
    """List local branches."""
    from kgn.git.branch import BranchService
    from kgn.git.service import GitService

    try:
        git_svc = GitService(target.resolve())
        branch_svc = BranchService(git_svc)
        current = branch_svc.current_branch()
        branches = branch_svc.list_branches()

        console.print("\n[bold]Branches[/bold]\n")
        for b in branches:
            marker = "[green]* " if b == current else "  "
            console.print(f"  {marker}{b}[/green]" if b == current else f"    {b}")
        console.print()

    except KgnError as exc:
        console.print(f"\n[bold red][{exc.code}] Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc


# ── branch checkout ───────────────────────────────────────────────────


@branch_app.command("checkout")
def branch_checkout(
    name: Annotated[str, typer.Argument(help="Branch name")],
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="Sync root directory"),
    ] = Path("./kgn-data"),
) -> None:
    """Switch to the specified branch."""
    from kgn.git.branch import BranchService
    from kgn.git.service import GitService

    try:
        git_svc = GitService(target.resolve())
        branch_svc = BranchService(git_svc)
        branch_svc.checkout(name)
        console.print(f"\n[green]Switched to branch '{name}'[/green]\n")
    except KgnError as exc:
        console.print(f"\n[bold red][{exc.code}] Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc


# ── branch cleanup ────────────────────────────────────────────────────


@branch_app.command("cleanup")
def branch_cleanup(
    target: Annotated[
        Path,
        typer.Option("--target", "-t", help="Sync root directory"),
    ] = Path("./kgn-data"),
) -> None:
    """Clean up merged agent branches."""
    from kgn.git.branch import BranchService
    from kgn.git.service import GitService

    try:
        git_svc = GitService(target.resolve())
        branch_svc = BranchService(git_svc)
        deleted = branch_svc.cleanup_merged_branches()

        if not deleted:
            console.print("\n[dim]No merged agent branches to clean up.[/dim]\n")
        else:
            console.print(f"\n[green]Cleaned up {len(deleted)} branch(es):[/green]")
            for b in deleted:
                console.print(f"  [dim]✗[/dim] {b}")
            console.print()

    except KgnError as exc:
        console.print(f"\n[bold red][{exc.code}] Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc


# ── pr create ─────────────────────────────────────────────────────────


@pr_app.command("create")
def pr_create(
    title: Annotated[str, typer.Option("--title", help="PR title")],
    head: Annotated[str, typer.Option("--head", help="Source branch")],
    base: Annotated[str, typer.Option("--base", help="Target branch")] = "main",
    body: Annotated[str, typer.Option("--body", help="PR description")] = "",
) -> None:
    """Create a GitHub Pull Request."""
    from kgn.github.client import GitHubClient, GitHubConfig
    from kgn.github.pr_service import PRContext, PullRequestService

    try:
        config = GitHubConfig.from_env()
        with GitHubClient(config) as client:
            pr_svc = PullRequestService(client)
            ctx = PRContext(
                task_title=title,
                task_id="manual",
                branch_name=head,
                base_branch=base,
                node_summary=body,
            )
            result = pr_svc.create_task_pr(ctx)

        if result.success:
            console.print(f"\n[green]PR #{result.pr_number} created[/green]")
            if result.html_url:
                console.print(f"  URL: {result.html_url}")
        else:
            console.print(f"\n[red]{result.message}[/red]")
            raise typer.Exit(code=1)
        console.print()

    except typer.Exit:
        raise
    except KgnError as exc:
        console.print(f"\n[bold red][{exc.code}] Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc


# ── pr list ───────────────────────────────────────────────────────────


@pr_app.command("list")
def pr_list(
    state: Annotated[str, typer.Option("--state", help="PR state (open|closed|all)")] = "open",
) -> None:
    """List GitHub Pull Requests."""
    from kgn.github.client import GitHubClient, GitHubConfig
    from kgn.github.pr_service import PullRequestService

    try:
        config = GitHubConfig.from_env()
        with GitHubClient(config) as client:
            pr_svc = PullRequestService(client)
            prs = pr_svc.list_prs(state=state)

        if not prs:
            console.print(f"\n[dim]No {state} PRs found.[/dim]\n")
            return

        console.print(f"\n[bold]Pull Requests ({state})[/bold]\n")
        for pr in prs:
            num = pr.get("number", "?")
            pr_title = pr.get("title", "")
            pr_state = pr.get("state", "")
            console.print(f"  [yellow]#{num}[/yellow]  {pr_title}  [dim]({pr_state})[/dim]")
        console.print()

    except typer.Exit:
        raise
    except KgnError as exc:
        console.print(f"\n[bold red][{exc.code}] Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}\n")
        raise typer.Exit(code=1) from exc
