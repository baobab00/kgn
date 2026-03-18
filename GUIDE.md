# KGN — Getting Started Guide

> Give your AI agents long-term memory with a shared knowledge graph.  
> This guide walks you through installing KGN, adding your first knowledge, and connecting to Claude — in under 10 minutes.

---

## Table of Contents

- [What is KGN?](#what-is-kgn)
- [Prerequisites](#prerequisites)
- [Step 1: Install KGN](#step-1-install-kgn)
- [Step 2: Start the Database](#step-2-start-the-database)
- [Step 3: Create a Project](#step-3-create-a-project)
- [Step 4: Write Your First .kgn File](#step-4-write-your-first-kgn-file)
- [Step 5: Ingest and Query](#step-5-ingest-and-query)
- [Step 6: Connect Claude](#step-6-connect-claude)
- [Going Further](#going-further)
  - [Relationships (.kge Files)](#relationships-kge-files)
  - [Semantic Search (Embeddings)](#semantic-search-embeddings)
  - [Web Dashboard](#web-dashboard)
  - [Task Queue & Multi-Agent](#task-queue--multi-agent)
  - [Git/GitHub Sync](#gitgithub-sync)
  - [VS Code Extension](#vs-code-extension)
- [CLI Quick Reference](#cli-quick-reference)
- [Node Types & Statuses](#node-types--statuses)
- [Troubleshooting](#troubleshooting)

---

## What is KGN?

AI agents forget everything between sessions. KGN fixes that.

KGN stores knowledge as **nodes** (ideas, specs, decisions, tasks) and **edges** (relationships between them) in a PostgreSQL database. AI agents can read, write, and search this knowledge through an MCP server, CLI, Web dashboard, or LSP-enabled editor.

**How it works:**

```
You write .kgn files  →  KGN stores them in PostgreSQL  →  AI agents can query anytime
```

---

## Prerequisites

| Required | Why | Install |
|---|---|---|
| **Python 3.12+** | KGN is a Python package | [python.org/downloads](https://www.python.org/downloads/) |
| **Docker Desktop** | Runs PostgreSQL for you | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) |
| **Git** | Needed to clone the Docker setup | Typically pre-installed |

> **Windows users:** When installing Python, check **"Add python.exe to PATH"** at the bottom of the installer.

---

## Step 1: Install KGN

```bash
pip install kgn-mcp
```

Verify it works:

```bash
kgn --version
# kgn version 0.12.0
```

> **Tip:** If `kgn` is not recognized, see [Troubleshooting](#kgn-command-not-found).

---

## Step 2: Start the Database

KGN needs PostgreSQL. The easiest way is Docker:

```bash
git clone https://github.com/baobab00/kgn.git
cd kgn
docker compose -f docker/docker-compose.yml up -d postgres
```

Verify it's running:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
# kgn-postgres   Up ...
```

> **What happened?** Docker downloaded PostgreSQL with the pgvector extension and started it on port 5433. It runs in the background — no window will appear.

---

## Step 3: Create a Project

```bash
kgn init --project my-project
```

This creates the database tables (if first run) and registers your project. You'll see a success message.

> **Tip:** `kgn init` also auto-generates a `.env` file with database connection settings if one doesn't exist.

---

## Step 4: Write Your First .kgn File

Create a file named `hello.kgn` with any text editor:

```yaml
---
kgn_version: "0.1"
id: "new:hello-world"
type: SPEC
status: ACTIVE
title: "Hello World"
project_id: "my-project"
agent_id: "human"
tags: ["getting-started"]
confidence: 0.9
---

## Context

This is my first knowledge node. KGN stores this in PostgreSQL
so any AI agent can find and reference it later.

## Content

Knowledge nodes use YAML front matter for metadata and Markdown for content.
Think of them as smart memos that AI can understand, search, and build upon.
```

**What each field means:**

| Field | Required | Meaning |
|---|---|---|
| `kgn_version` | Yes | Always `"0.1"` |
| `id` | Yes | `"new:slug"` for new nodes (auto-generates UUID), or an existing UUID |
| `type` | Yes | What kind of knowledge: GOAL, SPEC, TASK, DECISION, etc. |
| `status` | Yes | Lifecycle state: ACTIVE, DEPRECATED, SUPERSEDED, ARCHIVED |
| `title` | Yes | Human-readable title |
| `project_id` | Yes | Project this node belongs to |
| `agent_id` | Yes | Who created it (you, or an AI agent name) |
| `tags` | No | Labels for filtering and search |
| `confidence` | No | Certainty score (0.0–1.0) |

---

## Step 5: Ingest and Query

**Ingest** (store the node in the database):

```bash
kgn ingest hello.kgn --project my-project
```

**Check your project:**

```bash
kgn status --project my-project
```

**Search for nodes:**

```bash
kgn query nodes --project my-project --type SPEC
```

**See detailed health metrics:**

```bash
kgn health --project my-project
```

**Ingest the bundled examples:**

```bash
kgn ingest examples/ --project my-project --recursive
```

---

## Step 6: Connect Claude

This is where KGN becomes powerful — Claude can directly read, write, and manage tasks in your knowledge graph.

### Claude Code (recommended)

```bash
# Auto-generate .mcp.json in your project directory:
kgn mcp init --project my-project

# Or use Claude's CLI directly:
claude mcp add kgn -- kgn mcp serve --project my-project
```

### Claude Desktop

```bash
kgn mcp init --project my-project --target claude-desktop
```

Or manually add to your `claude_desktop_config.json`:

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "kgn": {
      "command": "kgn",
      "args": ["mcp", "serve", "--project", "my-project"]
    }
  }
}
```

Restart Claude. When you see the 🔨 icon, KGN tools are loaded.

Claude now has access to **12 MCP tools**:

| Tool | What it does |
|---|---|
| `get_node` | Read a node by ID |
| `query_nodes` | Search nodes (filter by type, status) |
| `get_subgraph` | Extract related nodes (BFS traversal) |
| `query_similar` | Find semantically similar nodes (vector search) |
| `ingest_node` | Create/update a node from .kgn text |
| `ingest_edge` | Create relationships from .kge text |
| `enqueue_task` | Add a task to the queue |
| `task_checkout` | Pick up the next task to work on |
| `task_complete` | Mark a task as done |
| `task_fail` | Mark a task as failed |
| `workflow_list` | List workflow templates |
| `workflow_run` | Execute a workflow (creates a task chain) |

---

## Going Further

### Relationships (.kge Files)

Nodes can be connected with typed edges. Create `edges.kge`:

```yaml
---
kgn_version: "0.1"
project_id: "my-project"
agent_id: "human"
edges:
  - from: "new:hello-world"
    to: "new:another-node"
    type: DEPENDS_ON
---
```

**Edge types:** `DEPENDS_ON`, `IMPLEMENTS`, `RESOLVES`, `SUPERSEDES`, `DERIVED_FROM`, `CONTRADICTS`, `CONSTRAINED_BY`

```bash
kgn ingest edges.kge --project my-project
```

---

### Semantic Search (Embeddings)

Enable AI-powered similarity search with OpenAI embeddings:

```bash
# Add your API key to .env
echo "KGN_OPENAI_API_KEY=sk-your-key-here" >> .env

# Test the connection
kgn embed provider test

# Ingest with embeddings
kgn ingest hello.kgn --project my-project --embed

# Find similar nodes
kgn query similar <node-uuid> --project my-project --top 5
```

> If no API key is set, KGN works normally — embeddings are silently skipped.

---

### Web Dashboard

Visualize your knowledge graph in a browser:

```bash
pip install kgn-mcp[web]
kgn web serve --project my-project --port 8080
```

Open http://localhost:8080 — interactive graph view, node details, task board, health dashboard, search & filter.

> **API key protection (optional):** Set `KGN_API_KEY=your-secret` as an environment variable to require an `X-API-Key` header for all API endpoints (except health).

---

### Task Queue & Multi-Agent

KGN includes a full task orchestration system for coordinating multiple AI agents:

```bash
# Enqueue a TASK node
kgn task enqueue <task-node-uuid> --project my-project

# Check out the next task (returns context package)
kgn task checkout --project my-project

# Complete or fail a task
kgn task complete <task-queue-id>
kgn task fail <task-queue-id> --reason "description"

# View all tasks
kgn task list --project my-project
```

**Workflow templates** create multi-step task chains automatically:

```bash
kgn workflow list
kgn workflow run design-to-impl --project my-project --trigger <node-uuid>
```

Available templates: `design-to-impl`, `issue-resolution`, `knowledge-indexing`

**Agent roles** control what each agent can do:

| Role | Can create | Description |
|---|---|---|
| `admin` | All types | Full access (default) |
| `genesis` | GOAL, SPEC, ARCH, CONSTRAINT, ASSUMPTION | Project design |
| `worker` | SPEC, ARCH, LOGIC, TASK, SUMMARY | Implementation |
| `reviewer` | DECISION, ISSUE, SUMMARY | Review & decisions |
| `indexer` | SUMMARY | Knowledge indexing |

```bash
kgn agent list --project my-project
kgn agent role --project my-project --agent-id <uuid> --role worker
```

---

### Git/GitHub Sync

KGN supports bidirectional sync between your database and Git:

```bash
# Export DB → filesystem
kgn sync export --project my-project --target ./sync

# Import filesystem → DB
kgn sync import --project my-project --source ./sync

# Push changes to GitHub
kgn sync push --project my-project --target ./sync

# Pull updates from GitHub
kgn sync pull --project my-project --target ./sync

# Generate Mermaid graph README
kgn graph mermaid --project my-project
kgn graph readme --project my-project --target ./sync
```

---

### VS Code Extension

Get syntax highlighting, diagnostics, and auto-completion for `.kgn` files:

```bash
code --install-extension baobab00.vscode-kgn
pip install kgn-mcp[lsp]    # for Language Server features
```

Features: syntax highlighting, real-time validation (V1–V10 rules), auto-completion, hover info, go-to-definition, CodeLens, subgraph preview panel.

---

## CLI Quick Reference

| What you want to do | Command |
|---|---|
| Create a project | `kgn init --project <name>` |
| Ingest a file | `kgn ingest <file.kgn> --project <name>` |
| Ingest a folder | `kgn ingest <folder>/ --project <name> --recursive` |
| Check project status | `kgn status --project <name>` |
| Graph health check | `kgn health --project <name>` |
| Search nodes | `kgn query nodes --project <name> [--type SPEC] [--status ACTIVE]` |
| Extract subgraph | `kgn query subgraph --project <name> --node-id <uuid>` |
| Similarity search | `kgn query similar <uuid> --project <name> --top 5` |
| Enqueue task | `kgn task enqueue <uuid> --project <name>` |
| Checkout task | `kgn task checkout --project <name>` |
| Complete task | `kgn task complete <task-queue-id>` |
| List tasks | `kgn task list --project <name>` |
| Start MCP server | `kgn mcp serve --project <name> [--role admin]` |
| Auto-config MCP | `kgn mcp init --project <name> [--target claude-desktop]` |
| Web dashboard | `kgn web serve --project <name> [--port 8080]` |
| Export to filesystem | `kgn sync export --project <name> --target ./sync` |
| Import from filesystem | `kgn sync import --project <name> --source ./sync` |
| Scan conflicts | `kgn conflict scan --project <name>` |
| List agents | `kgn agent list --project <name>` |
| Run workflow | `kgn workflow run <template> --project <name> --trigger <uuid>` |
| See all commands | `kgn --help` |

---

## Node Types & Statuses

### Types

| Type | Purpose |
|---|---|
| `GOAL` | High-level objective |
| `ARCH` | Architecture decision or design |
| `SPEC` | Specification or requirement |
| `LOGIC` | Implementation logic |
| `DECISION` | Explicit decision record |
| `ISSUE` | Problem or bug report |
| `TASK` | Actionable work item (can be queued) |
| `CONSTRAINT` | System constraint |
| `ASSUMPTION` | Documented assumption |
| `SUMMARY` | Generated summary or index |

### Statuses

| Status | Meaning |
|---|---|
| `ACTIVE` | Currently valid and in use |
| `DEPRECATED` | No longer recommended |
| `SUPERSEDED` | Replaced by another node |
| `ARCHIVED` | Kept for history |

### Edge Types

| Edge Type | Meaning |
|---|---|
| `DEPENDS_ON` | Source depends on target |
| `IMPLEMENTS` | Source implements target |
| `RESOLVES` | Source resolves target (issue/conflict) |
| `SUPERSEDES` | Source replaces target |
| `DERIVED_FROM` | Source is derived from target |
| `CONTRADICTS` | Source contradicts target |
| `CONSTRAINED_BY` | Source is constrained by target |

---

## Troubleshooting

<details>
<summary><b><code>kgn</code> command not found</b></summary>

Python's Scripts folder is not in your PATH.

**Windows:**
1. Press `Win + R`, type `sysdm.cpl`, and press Enter
2. Go to **Advanced** → **Environment Variables**
3. Add to **Path**: `C:\Users\<YourName>\AppData\Local\Programs\Python\Python312\Scripts`
4. Restart your terminal

**macOS/Linux:**
```bash
export PATH="$HOME/.local/bin:$PATH"
# Add to ~/.bashrc or ~/.zshrc to make it permanent
```

</details>

<details>
<summary><b>Docker: "Cannot connect to the Docker daemon"</b></summary>

Docker Desktop isn't running. Open Docker Desktop and wait for the whale icon in your taskbar to show "Running".

</details>

<details>
<summary><b>Database connection error</b></summary>

1. Check that the PostgreSQL container is running: `docker ps`
2. If not running: `docker compose -f docker/docker-compose.yml up -d postgres`
3. Check your `.env` file has the correct connection settings (auto-generated by `kgn init`)

</details>

<details>
<summary><b>"Permission denied" errors</b></summary>

**Windows:** Run your terminal as Administrator, or use `pip install --user kgn-mcp`.

**macOS/Linux:** Use `pip install --user kgn-mcp` or prefix with `sudo`.

</details>

<details>
<summary><b>MCP server not connecting to Claude</b></summary>

1. Ensure `kgn mcp serve --project <name>` works standalone in your terminal
2. Check your `claude_desktop_config.json` for syntax errors (valid JSON?)
3. Restart Claude Desktop completely
4. Look for the 🔨 icon — if missing, check Claude's MCP logs

</details>

---

**Need more help?** See the full [README](README.md) for details, the [Architecture Guide](ARCHITECTURE.md) for internals, or open an [issue on GitHub](https://github.com/baobab00/kgn/issues).
