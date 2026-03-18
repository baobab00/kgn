"""KGN CLI — LSP command: serve."""

from __future__ import annotations

from typing import Annotated

import typer

from kgn.cli._app import console, lsp_app

# ── lsp serve ─────────────────────────────────────────────────────────


@lsp_app.command()
def serve(
    tcp: Annotated[bool, typer.Option(help="Use TCP transport instead of stdio")] = False,
    host: Annotated[str, typer.Option(help="TCP host (only with --tcp)")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="TCP port (only with --tcp)")] = 2087,
) -> None:
    """Start the KGN Language Server.

    By default, communicates over stdio (standard input/output).
    Use --tcp for debugging with a TCP connection.
    """
    try:
        from kgn.lsp.server import server as lsp_server
    except ImportError as exc:
        console.print(
            "[bold red]Error:[/bold red] pygls is not installed. "
            "Install with: pip install 'kgn[lsp]'",
        )
        raise typer.Exit(code=1) from exc

    if tcp:
        console.print(f"[green]Starting KGN LSP server on tcp://{host}:{port}[/green]")
        lsp_server.start_tcp(host, port)
    else:
        lsp_server.start_io()
