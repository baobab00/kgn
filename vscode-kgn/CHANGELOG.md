# Changelog

All notable changes to the `vscode-kgn` extension will be documented in this file.

## [0.1.0] - 2026-03-04

Initial release.

### Added

- **TextMate Grammar** — Syntax highlighting for `.kgn` and `.kge` files
  - YAML front matter with KGN-specific key/value highlighting
  - Markdown body with full syntax support
  - Enum value highlighting (`type`, `status`, edge types)
- **Language Server Protocol (LSP)** — Advanced IDE features via `kgn lsp serve`
  - Real-time diagnostics (V1–V10 validation rules)
  - Auto-completion for `type`, `status`, and edge type enums
  - Hover information for node IDs and field descriptions
  - Go to Definition for referenced node UUIDs
  - CodeLens for reference counts per node
  - Semantic Tokens for enhanced highlighting
  - Subgraph preview panel (Mermaid)
- **Workspace Indexer** — Background scanning of `.kgn`/`.kge` files for cross-references
- **Snippets** — Quick templates for `.kgn` node and `.kge` edge files
- **Language Configuration** — Bracket matching, auto-closing, comment toggling
- **Settings** — `kgn.pythonPath`, `kgn.lsp.enabled`, `kgn.trace.server`
- **Commands** — `KGN: Show Graph Preview`

### Requirements

- VS Code 1.90.0+
- Python 3.12+ with `pip install kgn[lsp]` (for LSP features)
- TextMate syntax highlighting works without Python
