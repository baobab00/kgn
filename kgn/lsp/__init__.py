"""LSP support utilities for KGN files.

Submodules
----------
position
    Source-map and UTF-8 ↔ UTF-16 column conversion.
diagnostics
    DiagnosticSpan → LSP Diagnostic conversion.
tokens
    Semantic token types, modifiers, legend, and builder.
completion
    Context-aware YAML / Markdown completion provider.
hover
    Hover info and Go to Definition for UUIDs, slugs, ENUMs.
codelens
    Code Lens (reference counts, edge summaries) and Find References.
subgraph_handler
    Custom ``kgn/subgraph`` request — local subgraph JSON builder.
server
    pygls-based Language Server with incremental sync.
indexer
    Event-based incremental workspace indexer with O(1) lookups.
"""
