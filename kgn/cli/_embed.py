"""KGN CLI — embed commands: batch, provider-test."""

from __future__ import annotations

from typing import Annotated

import typer

from kgn.cli._app import _project_not_found, console, embed_app
from kgn.errors import KgnError

# ── embed batch ────────────────────────────────────────────────────────


@embed_app.command("batch")
def embed_batch(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    force: Annotated[
        bool,
        typer.Option("--force", help="Regenerate even already-embedded nodes"),
    ] = False,
) -> None:
    """Vectorize un-embedded nodes in the project."""
    from kgn.db.connection import close_pool, get_connection
    from kgn.db.repository import KgnRepository
    from kgn.embedding.factory import create_embedding_client

    client = create_embedding_client()
    if client is None:
        console.print(
            "\n[bold red]Error:[/bold red] KGN_OPENAI_API_KEY environment variable is not set.\n"
        )
        raise typer.Exit(code=1)

    try:
        with get_connection() as conn:
            repo = KgnRepository(conn)

            project_id = repo.get_project_by_name(project)
            if not project_id:
                _project_not_found(project)

            from kgn.embedding.service import EmbeddingService

            svc = EmbeddingService(repo=repo, client=client)
            count = svc.embed_batch(project_id, force=force)
            conn.commit()

        console.print(f"\n[green]✅ Embedded {count} nodes.[/green]\n")

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


# ── embed provider-test ────────────────────────────────────────────────


@embed_app.command("provider-test")
def embed_provider_test() -> None:
    """Test embedding provider connection — validate API key and call."""
    from kgn.embedding.factory import create_embedding_client

    client = create_embedding_client()
    if client is None:
        console.print(
            "\n[bold red]❌ Provider not configured.[/bold red]\n"
            "  Set the KGN_OPENAI_API_KEY environment variable.\n"
        )
        raise typer.Exit(code=1)

    console.print("\n[bold]Provider:[/bold] OpenAI")
    console.print(f"[bold]Model:[/bold]    {client.model}")
    console.print(f"[bold]Dimensions:[/bold] {client.dimensions}")
    console.print()

    try:
        vectors = client.embed(["KGN provider connectivity test"])
        if vectors and len(vectors[0]) == client.dimensions:
            console.print("[green]✅ Connection OK — embedding generated successfully.[/green]\n")
        else:
            console.print("[red]❌ Unexpected response shape.[/red]\n")
            raise typer.Exit(code=1)
    except KgnError as e:
        console.print(f"[bold red][{e.code}] Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"[red]❌ Connection failed: {e}[/red]\n")
        raise typer.Exit(code=1) from e
