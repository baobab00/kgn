# KGN — Knowledge Graph Node (VS Code Extension)

Syntax highlighting, real-time validation, and IDE support for `.kgn` (Knowledge Graph Node) and `.kge` (Knowledge Graph Edge) files.

## Features

### Syntax Highlighting
- **YAML front matter** — KGN-specific keys (`kgn_version`, `type`, `status`, etc.) with enum value highlighting
- **Markdown body** — Full Markdown syntax support after the closing `---`
- **Edge files** — `.kge` files with edge type enum highlighting

### Language Server (requires `kgn[lsp]`)
- Real-time diagnostics (V1–V10 validation rules)
- Auto-completion for `type`, `status`, and edge type enums
- Hover information for node IDs and slugs
- Go to Definition for referenced nodes
- CodeLens for reference counts
- Subgraph preview panel

## Requirements

- VS Code 1.90.0+
- Python 3.12+ with `pip install kgn[lsp]` (for LSP features)
- TextMate syntax highlighting works without Python

## Extension Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `kgn.pythonPath` | `""` | Path to Python interpreter. Empty = auto-detect. |
| `kgn.lsp.enabled` | `true` | Enable/disable the language server. |
| `kgn.trace.server` | `"off"` | Trace LSP communication (`off`, `messages`, `verbose`). |

## File Associations

| Extension | Language ID | Description |
|-----------|------------|-------------|
| `.kgn` | `kgn` | Knowledge Graph Node (YAML front matter + Markdown body) |
| `.kge` | `kge` | Knowledge Graph Edge (YAML only) |
