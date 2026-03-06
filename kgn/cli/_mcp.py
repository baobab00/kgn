"""KGN CLI — MCP command: serve."""

from __future__ import annotations

import os
from typing import Annotated

import typer
from rich.console import Console

from kgn.cli._app import console, mcp_app

# ── MCP serve ──────────────────────────────────────────────────────────


@mcp_app.command("serve")
def mcp_serve(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    transport: Annotated[
        str,
        typer.Option(
            "--transport",
            "-t",
            help="Transport (stdio | sse | streamable-http)",
        ),
    ] = os.environ.get("KGN_MCP_TRANSPORT", "stdio"),
    port: Annotated[
        int,
        typer.Option("--port", help="HTTP transport port number"),
    ] = int(os.environ.get("KGN_MCP_PORT", "8000")),
    role: Annotated[
        str,
        typer.Option(
            "--role",
            "-r",
            help="Agent role (genesis | worker | reviewer | indexer | admin)",
        ),
    ] = os.environ.get("KGN_AGENT_ROLE", "admin"),
) -> None:
    """Start the MCP server.

    Default transport is stdio (for Claude Desktop / VS Code integration).
    Use --transport sse or --transport streamable-http for HTTP server mode.
    """
    from kgn.mcp.server import create_server

    valid_transports = ("stdio", "sse", "streamable-http")
    if transport not in valid_transports:
        console.print(
            f"[red]Unsupported transport: {transport}[/red]\n"
            f"Available: {', '.join(valid_transports)}"
        )
        raise typer.Exit(code=1)

    # In MCP stdio mode, stdout is JSON-RPC channel so logs go to stderr
    from kgn.logging.config import configure_logging

    configure_logging(
        level=os.environ.get("KGN_LOG_LEVEL", "INFO"),
        fmt=os.environ.get("KGN_LOG_FORMAT", "json"),
        stderr=(transport == "stdio"),
    )

    valid_roles = ("genesis", "worker", "reviewer", "indexer", "admin")
    if role not in valid_roles:
        console.print(
            f"[red]Unsupported role: {role}[/red]\n"
            f"Available: {', '.join(valid_roles)}"
        )
        raise typer.Exit(code=1)

    server = create_server(project, role=role)

    if transport != "stdio":
        server.settings.port = port

    # In stdio transport, stdout is MCP JSON-RPC channel so output to stderr
    err_console = Console(stderr=True)
    err_console.print(
        f"[green]KGN MCP server started[/green] — "
        f"project=[bold]{project}[/bold]  transport={transport}"
        + (f"  port={port}" if transport != "stdio" else "")
    )
    server.run(transport=transport)  # type: ignore[arg-type]
