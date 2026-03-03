"""KGN Language Server — pygls-based LSP server with incremental sync.

Architecture
------------
* **Incremental Document Sync** — the default for pygls 2.x; clients send
  only the changed ranges instead of re-sending the full document.
* **Debounced Diagnostics** — a 300 ms debounce window prevents redundant
  re-parses during fast typing.  ``didSave`` bypasses the debounce.
* **Blocking Isolation (R23)** — ``parse_kgn_tolerant()`` is dispatched via
  ``asyncio.to_thread()`` to keep the event loop responsive under the GIL.
* **R24 invariant** — ``parse_kgn_tolerant()`` never raises.  The server
  wraps the diagnostic pipeline in a secondary safety net regardless.

Design rules respected
~~~~~~~~~~~~~~~~~~~~~~
R23  All blocking work → ``asyncio.to_thread``.
R24  ``parse_kgn_tolerant`` never throws; extra catch-all here as defence.
R25  TextMate = cosmetic.  This server provides the authoritative diagnostics.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kgn import __version__
from kgn.lsp.diagnostics import convert_diagnostics
from kgn.lsp.indexer import WorkspaceIndexer
from kgn.lsp.tokens import TOKEN_LEGEND, build_semantic_tokens
from kgn.parser import parse_kgn_tolerant

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

_DEBOUNCE_MS: int = 300
"""Default debounce interval for diagnostics (milliseconds)."""

_IO_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kgn-lsp-io")
"""Dedicated pool for blocking I/O work (R23)."""


# ── Server instance ───────────────────────────────────────────────────

server = LanguageServer(
    name="kgn-lsp",
    version=__version__,
    text_document_sync_kind=types.TextDocumentSyncKind.Incremental,
)

# URI → pending debounce Task
_pending_tasks: dict[str, asyncio.Task[None]] = {}

# Workspace indexer instance (shared across handlers)
indexer = WorkspaceIndexer()


# ── Lifecycle handlers ────────────────────────────────────────────────


@server.feature(types.INITIALIZED)
def _on_initialized(params: types.InitializedParams) -> None:  # noqa: ARG001
    logger.info("kgn-lsp initialized (v%s)", __version__)
    # Trigger initial workspace scan
    workspace_folders = server.workspace.folders
    if workspace_folders:
        first_folder = next(iter(workspace_folders.values()))
        root = _uri_to_path(first_folder.uri)
        if root is not None:
            asyncio.get_event_loop().create_task(_initial_scan(root))


async def _initial_scan(root: Path) -> None:
    """Run the initial full workspace scan."""
    try:
        await indexer.full_scan(root)
        logger.info(
            "Workspace scan complete: %d nodes, %d edge files",
            indexer.node_count,
            indexer.edge_file_count,
        )
    except Exception:
        logger.exception("Workspace scan failed")


@server.feature(types.SHUTDOWN)
def _on_shutdown(params: None) -> None:  # noqa: ARG001
    # Cancel all pending debounce tasks
    for task in _pending_tasks.values():
        task.cancel()
    _pending_tasks.clear()
    logger.info("kgn-lsp shutting down")


# ── Document sync handlers ────────────────────────────────────────────


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def _on_did_open(params: types.DidOpenTextDocumentParams) -> None:
    """Immediate diagnostics on open — no debounce."""
    uri = params.text_document.uri
    text = params.text_document.text
    _schedule_diagnostics(uri, text, debounce_ms=0)
    # Update indexer with open-document content
    path = _uri_to_path(uri)
    if path is not None and path.suffix.lower() == ".kgn":
        indexer.on_document_open(uri, path, text)


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def _on_did_change(params: types.DidChangeTextDocumentParams) -> None:
    """Debounced diagnostics on incremental change."""
    uri = params.text_document.uri
    doc = server.workspace.get_text_document(uri)
    _schedule_diagnostics(uri, doc.source, debounce_ms=_DEBOUNCE_MS)
    # Update indexer with changed buffer content
    path = _uri_to_path(uri)
    if path is not None and path.suffix.lower() == ".kgn":
        indexer.on_document_change(uri, path, doc.source)


@server.feature(types.TEXT_DOCUMENT_DID_SAVE)
def _on_did_save(params: types.DidSaveTextDocumentParams) -> None:
    """Immediate diagnostics on save — bypass debounce."""
    uri = params.text_document.uri
    doc = server.workspace.get_text_document(uri)
    _schedule_diagnostics(uri, doc.source, debounce_ms=0)


@server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
def _on_did_close(params: types.DidCloseTextDocumentParams) -> None:
    """Clean up on close — cancel pending diagnostics, clear published."""
    uri = params.text_document.uri
    _cancel_pending(uri)
    # Publish empty diagnostics to clear any existing markers
    server.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=uri, diagnostics=[]),
    )
    # Revert indexer to disk-based content
    path = _uri_to_path(uri)
    if path is not None:
        indexer.on_document_close(uri, path)


@server.feature(types.WORKSPACE_DID_CHANGE_WATCHED_FILES)
def _on_did_change_watched_files(
    params: types.DidChangeWatchedFilesParams,
) -> None:
    """Handle file system watcher events — incremental index updates."""
    for change in params.changes:
        path = _uri_to_path(change.uri)
        if path is None:
            continue

        if change.type == types.FileChangeType.Created:
            indexer.on_file_created(path)
        elif change.type == types.FileChangeType.Changed:
            indexer.on_file_changed(path)
        elif change.type == types.FileChangeType.Deleted:
            indexer.on_file_deleted(path)


# ── Semantic Tokens ───────────────────────────────────────────────────


@server.feature(
    types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    types.SemanticTokensOptions(
        legend=TOKEN_LEGEND,
        full=True,
    ),
)
async def _on_semantic_tokens_full(
    params: types.SemanticTokensParams,
) -> types.SemanticTokens | None:
    """Return full semantic tokens for a .kgn document.

    Dispatches the parse to a thread pool (R23) and converts the
    parser's ``yaml_node_positions`` into the LSP token format.
    """
    uri = params.text_document.uri
    doc = server.workspace.get_text_document(uri)
    text = doc.source

    if not isinstance(text, str):
        text = str(text)

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            _IO_POOL,
            parse_kgn_tolerant,
            text,
        )
        data = build_semantic_tokens(text, result)
    except Exception:
        logger.exception("Semantic tokens failed for %s", uri)
        data = []

    return types.SemanticTokens(data=data)


# ── Completion ────────────────────────────────────────────────────────


@server.feature(
    types.TEXT_DOCUMENT_COMPLETION,
    types.CompletionOptions(trigger_characters=[":", " ", "#"]),
)
def _on_completion(
    params: types.CompletionParams,
) -> types.CompletionList | None:
    """Provide context-aware completions for .kgn and .kge files."""
    from kgn.lsp.completion import get_completions

    uri = params.text_document.uri
    doc = server.workspace.get_text_document(uri)
    text = doc.source
    position = params.position

    is_kge = uri.lower().endswith(".kge")
    items = get_completions(text, position.line, position.character, is_kge=is_kge)
    return types.CompletionList(is_incomplete=False, items=items)


# ── Hover ─────────────────────────────────────────────────────────────


@server.feature(types.TEXT_DOCUMENT_HOVER)
def _on_hover(params: types.HoverParams) -> types.Hover | None:
    """Return Markdown hover info for UUIDs, slugs, and ENUM values."""
    from kgn.lsp.hover import get_hover

    uri = params.text_document.uri
    doc = server.workspace.get_text_document(uri)
    text = doc.source
    pos = params.position

    try:
        content = get_hover(text, pos.line, pos.character, indexer)
    except Exception:
        logger.exception("Hover failed for %s", uri)
        return None

    if content is None:
        return None

    return types.Hover(
        contents=types.MarkupContent(
            kind=types.MarkupKind.Markdown,
            value=content,
        ),
    )


# ── Go to Definition ─────────────────────────────────────────────────


@server.feature(types.TEXT_DOCUMENT_DEFINITION)
def _on_definition(
    params: types.DefinitionParams,
) -> types.Location | None:
    """Jump to the .kgn file for a UUID or new:slug reference."""
    from kgn.lsp.hover import get_definition

    uri = params.text_document.uri
    doc = server.workspace.get_text_document(uri)
    text = doc.source
    pos = params.position

    try:
        target_path = get_definition(text, pos.line, pos.character, indexer)
    except Exception:
        logger.exception("Definition lookup failed for %s", uri)
        return None

    if target_path is None:
        return None

    target_uri = target_path.as_uri()
    return types.Location(
        uri=target_uri,
        range=types.Range(
            start=types.Position(line=0, character=0),
            end=types.Position(line=0, character=0),
        ),
    )


# ── Code Lens ─────────────────────────────────────────────────────────


@server.feature(
    types.TEXT_DOCUMENT_CODE_LENS,
    types.CodeLensOptions(resolve_provider=False),
)
def _on_code_lens(
    params: types.CodeLensParams,
) -> list[types.CodeLens] | None:
    """Return code lenses for .kgn and .kge files."""
    from kgn.lsp.codelens import build_kge_lenses, build_kgn_lenses

    uri = params.text_document.uri
    doc = server.workspace.get_text_document(uri)
    text = doc.source
    file_path = _uri_to_path(uri)

    try:
        if file_path is None:
            return None

        if uri.lower().endswith(".kge"):
            lenses = build_kge_lenses(text, file_path, indexer)
        elif uri.lower().endswith(".kgn"):
            lenses = build_kgn_lenses(text, file_path, indexer)
        else:
            return None
    except Exception:
        logger.exception("Code lens failed for %s", uri)
        return None

    if not lenses:
        return None

    result: list[types.CodeLens] = []
    for lens in lenses:
        code_lens = types.CodeLens(
            range=types.Range(
                start=types.Position(line=lens.line, character=0),
                end=types.Position(line=lens.line, character=0),
            ),
        )
        if lens.command_id:
            code_lens.command = types.Command(
                title=lens.title,
                command=lens.command_id,
                arguments=lens.command_args,
            )
        else:
            code_lens.command = types.Command(
                title=lens.title,
                command="",
            )
        result.append(code_lens)

    return result


# ── Find References ───────────────────────────────────────────────────


@server.feature(types.TEXT_DOCUMENT_REFERENCES)
def _on_references(
    params: types.ReferenceParams,
) -> list[types.Location] | None:
    """Find all locations referencing the UUID/slug under cursor."""
    from kgn.lsp.codelens import find_references

    uri = params.text_document.uri
    doc = server.workspace.get_text_document(uri)
    text = doc.source
    pos = params.position

    try:
        refs = find_references(text, pos.line, pos.character, indexer)
    except Exception:
        logger.exception("Find references failed for %s", uri)
        return None

    if not refs:
        return None

    locations: list[types.Location] = []
    for ref in refs:
        locations.append(
            types.Location(
                uri=ref.path.as_uri(),
                range=types.Range(
                    start=types.Position(line=ref.line, character=ref.start_col),
                    end=types.Position(line=ref.line, character=ref.end_col),
                ),
            ),
        )
    return locations


# ── Custom: kgn/subgraph ───────────────────────────────────────────────


@server.feature("kgn/subgraph")
def _on_subgraph(params: list[object] | dict[str, object]) -> dict[str, object]:
    """Return a local subgraph centred on a node ID.

    Custom LSP request.  Parameters (dict or positional list):
        nodeId  — centre node identifier (UUID or new:slug).
        depth   — BFS depth (default 2).
        maxNodes — node cap (default 50).
    """
    from kgn.lsp.subgraph_handler import DEFAULT_DEPTH, DEFAULT_MAX_NODES, build_response

    try:
        if isinstance(params, dict):
            node_id = str(params.get("nodeId", ""))
            depth = int(params.get("depth", DEFAULT_DEPTH))
            max_nodes = int(params.get("maxNodes", DEFAULT_MAX_NODES))
        else:
            node_id = str(params[0]) if params else ""
            depth = int(params[1]) if len(params) > 1 else DEFAULT_DEPTH  # type: ignore[arg-type]
            max_nodes = int(params[2]) if len(params) > 2 else DEFAULT_MAX_NODES  # type: ignore[arg-type]

        return build_response(node_id, indexer, depth=depth, max_nodes=max_nodes)  # type: ignore[return-value]
    except Exception:
        logger.exception("kgn/subgraph failed")
        return {"centre": "", "nodes": [], "edges": [], "truncated": False}  # type: ignore[return-value]


# ── Debounce engine ───────────────────────────────────────────────────


def _uri_to_path(uri: str) -> Path | None:
    """Convert a ``file://`` URI to a :class:`Path`, or ``None``.

    Handles both Windows (``file:///C:/...``) and Unix (``file:///home/...``)
    URIs correctly, including percent-encoded characters.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    raw = unquote(parsed.path)
    # On Windows, parsed.path starts with /C: — strip leading /
    if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
        raw = raw[1:]
    return Path(raw)


def _schedule_diagnostics(uri: str, text: str, *, debounce_ms: int) -> None:
    """Schedule a diagnostic run with optional debounce.

    * If *debounce_ms* ≤ 0 the parse runs immediately (via the event loop).
    * Otherwise a delayed Task is created; any previous pending Task for the
      same URI is cancelled first so only the last keystroke triggers work.
    """
    _cancel_pending(uri)
    loop = asyncio.get_event_loop()
    task = loop.create_task(_debounced_run(uri, text, debounce_ms))
    _pending_tasks[uri] = task


def _cancel_pending(uri: str) -> None:
    """Cancel and remove any pending debounce Task for *uri*."""
    prev = _pending_tasks.pop(uri, None)
    if prev is not None and not prev.done():
        prev.cancel()


async def _debounced_run(uri: str, text: str, debounce_ms: int) -> None:
    """Wait for the debounce window then run diagnostics."""
    try:
        if debounce_ms > 0:
            await asyncio.sleep(debounce_ms / 1000.0)
        await _run_diagnostics(uri, text)
    except asyncio.CancelledError:
        pass  # Expected — a newer keystroke cancelled this task
    except Exception:
        logger.exception("Unexpected error in diagnostic pipeline for %s", uri)
    finally:
        _pending_tasks.pop(uri, None)


# ── Diagnostic pipeline ──────────────────────────────────────────────


async def _run_diagnostics(uri: str, text: str) -> None:
    """Parse *text* in a thread (R23) and publish diagnostics."""
    # Coerce to str as defence-in-depth (R24)
    if not isinstance(text, str):
        text = str(text)

    # Dispatch blocking parse to thread pool
    result = await asyncio.get_event_loop().run_in_executor(
        _IO_POOL,
        parse_kgn_tolerant,
        text,
    )

    # Convert DiagnosticSpan → LSP Diagnostic
    lsp_diags = convert_diagnostics(result.diagnostics, text)

    server.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=uri, diagnostics=lsp_diags),
    )
