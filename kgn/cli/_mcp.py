"""KGN CLI — MCP commands: init, serve."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from kgn.cli._app import console, mcp_app

# ── MCP init ───────────────────────────────────────────────────────────


def _find_uv() -> str:
    """Return absolute path to uv executable."""
    uv = shutil.which("uv")
    if uv:
        return str(Path(uv).resolve())
    # Windows common location
    candidate = Path.home() / ".local" / "bin" / "uv.exe"
    if candidate.is_file():
        return str(candidate)
    return "uv"


def _kgn_source_dir() -> str:
    """Return the kgn package source directory (for --directory)."""
    return str(Path(__file__).resolve().parents[1])


@mcp_app.command("init")
def mcp_init(
    project: Annotated[str, typer.Option("--project", "-p", help="Project name")],
    target: Annotated[
        str,
        typer.Option(
            "--target",
            "-t",
            help="Target client (claude-code | claude-desktop)",
        ),
    ] = "claude-code",
    role: Annotated[
        str,
        typer.Option(
            "--role",
            "-r",
            help="Agent role (genesis | worker | reviewer | indexer | admin)",
        ),
    ] = "admin",
    output: Annotated[
        str | None,
        typer.Option(
            "--output",
            "-o",
            help="Output directory (default: CWD for claude-code, "
            "APPDATA/Claude for claude-desktop)",
        ),
    ] = None,
) -> None:
    """Generate MCP client configuration file.

    For Claude Code: creates .mcp.json in the project directory.
    For Claude Desktop: creates/updates claude_desktop_config.json.
    """
    valid_targets = ("claude-code", "claude-desktop")
    if target not in valid_targets:
        console.print(
            f"[red]Unsupported target: {target}[/red]\nAvailable: {', '.join(valid_targets)}"
        )
        raise typer.Exit(code=1)

    valid_roles = ("genesis", "worker", "reviewer", "indexer", "admin")
    if role not in valid_roles:
        console.print(f"[red]Unsupported role: {role}[/red]\nAvailable: {', '.join(valid_roles)}")
        raise typer.Exit(code=1)

    uv_path = _find_uv()
    kgn_dir = _kgn_source_dir()

    args = [
        "--directory",
        kgn_dir,
        "run",
        "kgn",
        "mcp",
        "serve",
        "--project",
        project,
    ]
    if role != "admin":
        args.extend(["--role", role])

    server_entry = {"command": uv_path, "args": args}

    if target == "claude-code":
        out_dir = Path(output) if output else Path.cwd()
        config_path = out_dir / ".mcp.json"
        config: dict = {}
        if config_path.is_file():
            config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("mcpServers", {})["kgn"] = server_entry
        config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        console.print(
            f"[green]Created[/green] {config_path}\n  project=[bold]{project}[/bold]  role={role}"
        )
    else:
        # claude-desktop
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA", "")
            default_dir = Path(appdata) / "Claude" if appdata else None
        elif sys.platform == "darwin":
            default_dir = Path.home() / "Library" / "Application Support" / "Claude"
        else:
            default_dir = Path.home() / ".config" / "claude"

        out_dir = Path(output) if output else default_dir
        if out_dir is None:
            console.print("[red]Cannot determine APPDATA path[/red]")
            raise typer.Exit(code=1)

        out_dir.mkdir(parents=True, exist_ok=True)
        config_path = out_dir / "claude_desktop_config.json"
        config = {}
        if config_path.is_file():
            config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("mcpServers", {})["kgn"] = server_entry
        config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        console.print(
            f"[green]Created[/green] {config_path}\n  project=[bold]{project}[/bold]  role={role}"
        )


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
        console.print(f"[red]Unsupported role: {role}[/red]\nAvailable: {', '.join(valid_roles)}")
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
