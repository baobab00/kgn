# kgn

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/kgn-banner-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="assets/kgn-banner-light.png">
  <img alt="KGN — Knowledge Graph Node" src="assets/kgn-banner-light.png" width="100%">
</picture>

[![CI](https://github.com/baobab00/kgn/actions/workflows/ci.yml/badge.svg)](https://github.com/baobab00/kgn/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/kgn-mcp?v=1)](https://pypi.org/project/kgn-mcp/)
[![Python 3.12+](https://img.shields.io/pypi/pyversions/kgn-mcp?v=1)](https://pypi.org/project/kgn-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![codecov](https://codecov.io/gh/baobab00/kgn/graph/badge.svg)](https://codecov.io/gh/baobab00/kgn)
[![Tests](https://img.shields.io/badge/tests-2029%2B%20passing-brightgreen)](https://github.com/baobab00/kgn/actions)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> **Manage your AI agent's knowledge — parse, store, query, and collaborate.**

KGN is a developer-friendly CLI + MCP server for teams building with AI agents.
Write knowledge nodes in simple YAML+Markdown files (`.kgn`), define relationships
between them (`.kge`), and let KGN handle storage, similarity search, conflict
detection, and multi-agent task handoffs — all backed by PostgreSQL + pgvector.

**Hybrid architecture:** PostgreSQL is the local working engine; GitHub is the
long-term source of truth. Export, commit, and push in one command.

---

## Table of Contents

- [Why KGN?](#why-kgn)
- [Quick Start](#quick-start)
- [MCP Server (Claude Integration)](#10-mcp-server-claude-integration)
- [Multi-Agent Orchestration](#multi-agent-orchestration)
- [CLI Commands](#cli-commands)
- [File Formats](#file-formats)
- [Development](#development)
- [Tech Stack](#tech-stack)

---

## Why KGN?

AI agents are powerful, but they forget everything between sessions — and when multiple agents collaborate, they can conflict, duplicate work, or lose track of decisions.

KGN gives your agents a **shared, queryable memory**:

| Problem | KGN Solution |
|---|---|
| Agents forget past decisions | Persistent knowledge graph in PostgreSQL |
| Duplicate work across agents | Conflict detection + similarity search |
| No task coordination | Built-in task queue with lease management |
| Hard to audit agent actions | Structured activity log per agent |
| Context window overflow | Subgraph extraction — only what's relevant |
| IDE friction for `.kgn` files | VS Code extension with LSP support |

---

## Quick Start

### 1. Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker / Docker Compose (PostgreSQL)

> **Optional dependency:** Embedding features (`kgn embed`, `kgn ingest --embed`) and similarity search (`kgn query similar`) require the `openai` package. Core ingest/query/health features work without it.

### Embedding Provider Setup

To use embedding features, set your OpenAI API key in the `.env` file:

```bash
# .env
KGN_OPENAI_API_KEY=sk-your-api-key-here
KGN_OPENAI_EMBED_MODEL=text-embedding-3-small    # default
```

If the API key is not set, ingest works normally and embedding is silently skipped (graceful degradation).

```bash
# Test provider connection
kgn embed provider test
```

### 2. Installation

```bash
# Method A: pip install (recommended)
pip install kgn-mcp

# Method B: Source install (development)
git clone https://github.com/baobab00/kgn.git
cd kgn
uv sync

# Optional: OpenAI embedding support
pip install kgn-mcp[openai]
```

```bash
# Start PostgreSQL container (DB only)
docker compose -f docker/docker-compose.yml up -d postgres
```

#### Docker All-in-One (optional)

Run PostgreSQL + kgn CLI together with Docker:

```bash
# Build + start
docker compose -f docker/docker-compose.yml up -d --build

# Use kgn CLI
docker compose -f docker/docker-compose.yml exec kgn kgn init --project my-project
docker compose -f docker/docker-compose.yml exec kgn kgn --help

# Place .kgn/.kge files in docker/workspace/ directory
```

### 3. Initialize DB

```bash
kgn init --project my-project
```

### 4. Ingest Files

```bash
# Single file
kgn ingest my-spec.kgn --project my-project

# Entire directory (recursive)
kgn ingest ./specs/ --project my-project --recursive

# Example files
kgn ingest examples/ --project my-project --recursive
```

### 5. Project Status

```bash
kgn status --project my-project
```

### 6. Query

```bash
# Search nodes
kgn query nodes --project my-project --type SPEC --status ACTIVE

# Extract subgraph (JSON)
kgn query subgraph <node-uuid> --project my-project --depth 2 --format json

# Extract subgraph (Markdown)
kgn query subgraph <node-uuid> --project my-project --format md
```

### 7. Graph Health

```bash
kgn health --project my-project
```

### 8. Semantic Search

```bash
# Ingest + auto-embed
kgn ingest examples/ --project my-project --recursive --embed

# Backfill embeddings for existing nodes
kgn embed --project my-project

# Find similar nodes
kgn query similar <node-uuid> --project my-project --top 5
```

### 9. Conflict Detection

```bash
# Scan for conflict candidates
kgn conflict scan --project my-project

# Approve/dismiss conflicts
kgn conflict approve <edge-id> --project my-project
kgn conflict dismiss <edge-id> --project my-project
```

### 10. MCP Server (Claude Integration)

The MCP (Model Context Protocol) server enables Claude to directly read, write, and manage tasks in the knowledge graph.

```bash
# stdio mode (Claude Desktop / Claude Code default)
kgn mcp serve --project my-project

# HTTP SSE mode
KGN_MCP_TRANSPORT=sse KGN_MCP_PORT=8000 kgn mcp serve --project my-project

# streamable-http mode
KGN_MCP_TRANSPORT=streamable-http kgn mcp serve --project my-project
```

**Claude Desktop integration** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kgn": {
      "command": "uv",
      "args": ["run", "kgn", "mcp", "serve", "--project", "my-project"]
    }
  }
}
```

**MCP Tools** (12 tools):

| Tool | Category | Description |
|---|---|---|
| `get_node` | Read | Get node by ID |
| `query_nodes` | Read | Search nodes in project (type/status filter) |
| `get_subgraph` | Read | BFS subgraph extraction from node |
| `query_similar` | Read | Vector similarity Top-K search |
| `task_checkout` | Task | Check out highest-priority task (with auto lease recovery) |
| `task_complete` | Task | Mark task as complete (auto-unblocks dependent tasks) |
| `task_fail` | Task | Mark task as failed |
| `workflow_list` | Workflow | List registered workflow templates |
| `workflow_run` | Workflow | Execute a workflow template (creates subtask DAG) |
| `ingest_node` | Write | Ingest node from .kgn string |
| `ingest_edge` | Write | Ingest edge from .kge string |
| `enqueue_task` | Write | Enqueue TASK node |

### 11. Git/GitHub Sync

```bash
# Export DB → filesystem (+ auto-generate Mermaid README)
kgn sync export --project my-project --target ./sync

# Import filesystem → DB
kgn sync import --project my-project --source ./sync

# Push/pull to GitHub
kgn sync push --project my-project --target ./sync
kgn sync pull --project my-project --target ./sync

# Git repository management
kgn git init --target ./sync
kgn git status --target ./sync
kgn git log --target ./sync

# Mermaid visualization
kgn graph mermaid --project my-project
kgn graph readme --project my-project --target ./sync

# Branch/PR management
kgn git branch list --target ./sync
kgn git pr create --project my-project --target ./sync --title "PR title"
```

### 12. Web Dashboard

```bash
# Install with web dependencies
pip install kgn-mcp[web]

# Start the web dashboard
kgn web serve --project my-project --port 8080
```

Open http://localhost:8080 in your browser to:
- **Graph View** — Explore the knowledge graph interactively (Cytoscape.js)
- **Node Detail** — Click any node to see front matter, body, and edge relationships
- **Task Board** — Monitor task queue via Kanban board (READY / IN_PROGRESS / BLOCKED / DONE / FAILED)
- **Health Dashboard** — Track graph health metrics with Chart.js visualizations
- **Search & Filter** — Filter by type/status/tags, find similar nodes, detect conflicts

> Requires: `pip install kgn-mcp[web]` (fastapi, uvicorn, jinja2)

### 13. VS Code Extension

The `vscode-kgn` extension provides IDE-level support for `.kgn` and `.kge` files.

```bash
# Install LSP dependencies
pip install kgn-mcp[lsp]

# Install extension (from .vsix)
code --install-extension vscode-kgn-0.1.0.vsix
```

**Features:**
- **Syntax Highlighting** — TextMate grammar for YAML front matter + Markdown body
- **Real-time Diagnostics** — V1–V10 validation rules via Language Server
- **Auto-completion** — `type`, `status`, and edge type enums
- **Hover** — Node ID resolution and field descriptions
- **Go to Definition** — Navigate to referenced nodes
- **CodeLens** — Reference counts per node
- **Subgraph Preview** — Mermaid-based graph visualization

> TextMate syntax highlighting works without Python. LSP features require `pip install kgn-mcp[lsp]`.

### Error Code System

All MCP error responses are returned as structured JSON:

```json
{
  "error": "Error message",
  "code": "KGN-300",
  "detail": "Detailed description",
  "recoverable": false
}
```

| Code | Category | Description | Retryable |
|---|---|---|---|
| `KGN-100` | Infrastructure | Database connection failed | ✅ |
| `KGN-101` | Infrastructure | Embedding provider unavailable | ✅ |
| `KGN-200` | Ingest | YAML front matter parse error | ❌ |
| `KGN-201` | Ingest | Required field missing | ❌ |
| `KGN-202` | Ingest | Invalid field value | ❌ |
| `KGN-300` | Query | Node not found | ❌ |
| `KGN-301` | Query | Invalid UUID format | ❌ |
| `KGN-302` | Query | Subgraph depth limit exceeded | ❌ |
| `KGN-400` | Task | No READY tasks available | ❌ |
| `KGN-401` | Task | Task not in expected state | ❌ |
| `KGN-402` | Task | Lease expired | ✅ |
| `KGN-999` | Internal | Unexpected server error | ✅ |

## Multi-Agent Orchestration

KGN supports multi-agent collaborative workflows where multiple AI agents work together on a knowledge graph with role-based access control, task handoff, and conflict resolution.

### Agent Roles

| Role | Create | Checkout | Description |
|---|---|---|---|
| **genesis** | GOAL, SPEC, ARCH, CONSTRAINT, ASSUMPTION | — | Project bootstrapping |
| **worker** | SPEC, ARCH, LOGIC, TASK, SUMMARY | ✅ (role-filtered) | Implementation work |
| **reviewer** | DECISION, ISSUE, SUMMARY | ✅ (role-filtered) | Code review & decisions |
| **indexer** | SUMMARY | — | Knowledge indexing |
| **admin** | All types | ✅ (all tasks) | Full access |

### Workflow Engine

Built-in workflow templates orchestrate multi-step processes:

| Template | Steps | Description |
|---|---|---|
| `design-to-impl` | GOAL → SPEC → ARCH → TASK(impl) → TASK(review) | Full design-to-implementation pipeline |
| `issue-resolution` | ISSUE → TASK(fix) → TASK(verify) | Bug fix workflow |
| `knowledge-indexing` | GOAL → TASK(index) → TASK(review) | Knowledge capture pipeline |

```bash
# Execute a workflow via MCP
# workflow_run(project_id, trigger_node_id, template_name)
```

### Key Features

- **Task Handoff** — Automatic context propagation between workflow steps (impl → review)
- **Advisory Locking** — `NodeLockService` prevents concurrent modifications to the same node
- **Conflict Resolution** — Detects when multiple agents modify the same node; auto-creates review tasks
- **Observability** — Agent activity timeline, task flow stats, bottleneck detection

### Agent CLI

```bash
# List registered agents
kgn agent list --project my-project

# Set agent role
kgn agent role --project my-project --agent-id <uuid> --role worker

# View agent statistics
kgn agent stats --project my-project --agent-id <uuid>

# View agent activity timeline
kgn agent timeline --project my-project --agent-id <uuid>
```

## CLI Commands

> Run `kgn --help` for the full command list.

Key commands summary:

| Group | Example | Description |
|---|---|---|
| **Core** | `kgn init`, `kgn ingest`, `kgn status`, `kgn health` | Initialize, ingest, status, health |
| **Query** | `kgn query nodes`, `kgn query subgraph`, `kgn query similar` | Search, subgraph, similarity |
| **Task** | `kgn task enqueue/checkout/complete/fail/list/log` | Task orchestration |
| **Embed** | `kgn embed`, `kgn embed provider test` | Embedding management |
| **Conflict** | `kgn conflict scan/approve/dismiss` | Conflict detection/management |
| **Sync** | `kgn sync export/import/status/push/pull` | DB ↔ file ↔ GitHub sync |
| **Git** | `kgn git init/status/diff/log/branch/pr` | Git/GitHub management |
| **Graph** | `kgn graph mermaid/readme` | Mermaid visualization |
| **MCP** | `kgn mcp serve` | MCP server (stdio/sse/streamable-http) |
| **Agent** | `kgn agent list/role/stats/timeline` | Multi-agent orchestration |
| **Web** | `kgn web serve` | Web visualization dashboard |
| **LSP** | `kgn lsp serve` | Language Server (VS Code integration) |

## Expired Task Recovery

When a checked-out task exceeds its `lease_expires_at`, it is considered **expired**.
`requeue_expired` resets expired `IN_PROGRESS` tasks to `READY` and increments `attempts`.

| Method | Description |
|---|---|
| **MCP** | `checkout` automatically calls `requeue_expired` beforehand |
| **CLI** | Manual invocation or cron schedule required (call before `kgn task checkout`) |

> When `max_attempts` (default 3) is exceeded, the task transitions to `FAILED` and is excluded from automatic recovery.

## File Formats

### `.kgn` — Knowledge Graph Node

```yaml
---
kgn_version: "0.1"
id: "new:my-node"        # UUID or new:slug
type: SPEC               # GOAL, ARCH, SPEC, LOGIC, DECISION, ISSUE, TASK, CONSTRAINT, ASSUMPTION, SUMMARY
title: "Node title"
status: ACTIVE            # ACTIVE, DEPRECATED, SUPERSEDED, ARCHIVED
project_id: "my-project"
agent_id: "my-agent"
tags: ["tag1", "tag2"]
confidence: 0.9
---

## Context
...
## Content
...
```

### `.kge` — Edge Definition

```yaml
---
kgn_version: "0.1"
project_id: "my-project"
agent_id: "my-agent"
edges:
  - from: "new:node-a"
    to:   "new:node-b"
    type: DEPENDS_ON      # DEPENDS_ON, IMPLEMENTS, RESOLVES, SUPERSEDES, DERIVED_FROM, CONTRADICTS, CONSTRAINED_BY
    note: "Edge description"
---
```

## Example Files

The `examples/` directory contains practical examples:

| File | Description |
|---|---|
| `goal-example.kgn` | GOAL type node example |
| `spec-example.kgn` | SPEC type node example |
| `decision-example.kgn` | DECISION type node example |
| `edges-example.kge` | Edge definition example (IMPLEMENTS, DERIVED_FROM) |

## Development

```bash
# Lint
uv run ruff check .

# Format
uv run ruff format .

# Test
uv run pytest --tb=short -q

# Coverage
uv run pytest --cov=kgn --cov-report=term-missing
```

## Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.12+ |
| **CLI** | [Typer](https://typer.tiangolo.com/) + [Rich](https://rich.readthedocs.io/) |
| **DB** | PostgreSQL 16 + [pgvector](https://github.com/pgvector/pgvector) |
| **ORM/SQL** | [psycopg3](https://www.psycopg.org/psycopg3/) (native async-ready) |
| **Validation** | [Pydantic v2](https://docs.pydantic.dev/) |
| **AI Protocol** | [MCP 1.26.0](https://modelcontextprotocol.io/) via FastMCP |
| **Embeddings** | OpenAI `text-embedding-3-small` (optional) |
| **Git/GitHub** | bidirectional sync (DB \u2194 GitHub) |
| **Logging** | [structlog](https://www.structlog.org/) (JSON / console) |
| **Web** | FastAPI + Uvicorn + Jinja2 + Cytoscape.js (optional extra) |
| **IDE** | VS Code extension + pygls LSP (optional extra) |
| **Infra** | Docker Compose + GitHub Actions CI |
| **Quality** | [ruff](https://docs.astral.sh/ruff/) + pytest (2031 tests, 93%+ coverage) |

## License

MIT
