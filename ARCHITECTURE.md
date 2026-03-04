# KGN Architecture

> KGN is a CLI + MCP server that gives AI agents **persistent, queryable memory** — backed by PostgreSQL and pgvector.  
> This document provides a comprehensive visual guide to KGN's internal architecture using Mermaid diagrams.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Layer Architecture](#2-layer-architecture)
3. [Module Dependency Graph](#3-module-dependency-graph)
4. [Database Schema](#4-database-schema)
5. [Ingest Pipeline](#5-ingest-pipeline)
6. [Task Lifecycle](#6-task-lifecycle)
7. [Task Checkout — Context Package](#7-task-checkout--context-package)
8. [Embedding & Similarity Search](#8-embedding--similarity-search)
9. [Interface Layer](#9-interface-layer)
10. [MCP Server Tools](#10-mcp-server-tools)
11. [LSP Server Capabilities](#11-lsp-server-capabilities)
12. [Orchestration Layer](#12-orchestration-layer)
13. [Sync & GitHub Integration](#13-sync--github-integration)
14. [Conflict Detection](#14-conflict-detection)
15. [Workflow Engine](#15-workflow-engine)
16. [End-to-End Data Flow](#16-end-to-end-data-flow)

---

## 1. System Overview

A bird's-eye view of the entire KGN system — from file input to interface output.

```mermaid
graph TB
    subgraph "File Format"
        KGN_FILE[".kgn files"]
        KGE_FILE[".kge files"]
    end

    subgraph "Core"
        PARSER["Parser"]
        MODELS["Models<br/>(Pydantic)"]
        SERIALIZER["Serializer"]
    end

    subgraph "Storage"
        REPO["KgnRepository<br/>(Single SQL Source)"]
        PG["PostgreSQL"]
        PGVEC["pgvector<br/>(1536-dim HNSW)"]
    end

    subgraph "Services"
        INGEST["IngestService"]
        GRAPH["Graph Services"]
        EMBED["EmbeddingService"]
        TASK["TaskService"]
        CONFLICT["ConflictService"]
        SYNC["SyncService"]
    end

    subgraph "Orchestration"
        ROLES["RoleGuard"]
        WORKFLOW["WorkflowEngine"]
        LOCK["NodeLockService"]
        HANDOFF["HandoffService"]
    end

    subgraph "Interfaces"
        CLI["CLI<br/>(Typer)"]
        MCP["MCP Server<br/>(FastMCP)"]
        LSP["LSP Server<br/>(pygls)"]
        WEB["Web Dashboard<br/>(FastAPI)"]
    end

    subgraph "Integrations"
        GIT["Git<br/>(subprocess)"]
        GITHUB["GitHub<br/>(REST API)"]
    end

    KGN_FILE --> PARSER
    KGE_FILE --> PARSER
    PARSER --> MODELS
    MODELS --> INGEST
    SERIALIZER --> MODELS

    INGEST --> REPO
    REPO --> PG
    REPO --> PGVEC

    REPO --> GRAPH
    REPO --> EMBED
    REPO --> TASK
    REPO --> CONFLICT
    REPO --> SYNC

    TASK --> WORKFLOW
    TASK --> LOCK
    TASK --> HANDOFF
    WORKFLOW --> ROLES

    GRAPH --> CLI
    GRAPH --> MCP
    GRAPH --> LSP
    GRAPH --> WEB

    TASK --> CLI
    TASK --> MCP

    EMBED --> CLI
    EMBED --> MCP

    SYNC --> GIT
    GIT --> GITHUB
```

---

## 2. Layer Architecture

KGN is organized into 7 layers. Each layer only depends on the layers below it.

| Layer | Responsibility | Key Technologies |
|---|---|---|
| **Layer 0** — File Format | Custom `.kgn`/`.kge` (YAML frontmatter + Markdown) | — |
| **Layer 1** — Core | Parsing, data models, serialization, errors | Pydantic v2 |
| **Layer 2** — Storage | All SQL queries, connection pooling, migrations | PostgreSQL, pgvector, psycopg3 |
| **Layer 3** — Services | Business logic: ingest, graph, embedding, task, conflict, sync | OpenAI API |
| **Layer 4** — Orchestration | Multi-agent coordination: roles, workflows, locking, handoff | — |
| **Layer 5** — Interfaces | Four access points: CLI, MCP, LSP, Web | Typer, FastMCP, pygls, FastAPI |
| **Layer 6** — Integrations | Version control and remote sync | Git, GitHub REST API |

```mermaid
graph TB
    L0["<b>Layer 0 — File Format</b><br/>.kgn / .kge"]
    L1["<b>Layer 1 — Core</b><br/>Parser · Models · Serializer · Errors"]
    L2["<b>Layer 2 — Storage</b><br/>PostgreSQL + pgvector · Repository · Migrations"]
    L3["<b>Layer 3 — Services</b><br/>Ingest · Graph · Embedding · Task · Conflict · Sync"]
    L4["<b>Layer 4 — Orchestration</b><br/>Roles · Workflow · Locking · Handoff · Matching · Observability"]
    L5["<b>Layer 5 — Interfaces</b><br/>CLI (Typer) · MCP (FastMCP) · LSP (pygls) · Web (FastAPI)"]
    L6["<b>Layer 6 — Integrations</b><br/>Git (subprocess) · GitHub (REST API)"]

    L0 --> L1
    L1 --> L2
    L2 --> L3
    L3 --> L4
    L4 --> L5
    L5 --> L6

    style L0 fill:#f9f,stroke:#333
    style L1 fill:#bbf,stroke:#333
    style L2 fill:#fbb,stroke:#333
    style L3 fill:#bfb,stroke:#333
    style L4 fill:#fbf,stroke:#333
    style L5 fill:#ff9,stroke:#333
    style L6 fill:#9ff,stroke:#333
```

---

## 3. Module Dependency Graph

Every module in `kgn/` and its direct dependencies. Arrows point from consumer to dependency.

- **Core modules** (`models`, `parser`, `serializer`, `errors`) have no circular dependencies.
- **`db/repository.py`** is the single SQL source — all services depend on it.
- **Interface modules** (`cli`, `mcp`, `lsp`, `web`) sit at the top, consuming services.

```mermaid
graph TD
    ERRORS["errors.py"]
    MODELS["models/"]
    PARSER["parser/"]
    SERIALIZER["serializer/"]
    INGEST["ingest/"]
    DB_CONN["db/connection.py"]
    DB_REPO["db/repository.py"]
    DB_MIG["db/migrations.py"]
    MIGRATIONS["migrations/"]
    GRAPH_SUB["graph/subgraph.py"]
    GRAPH_HEALTH["graph/health.py"]
    GRAPH_MERMAID["graph/mermaid.py"]
    EMBED["embedding/"]
    TASK["task/"]
    CONFLICT["conflict/"]
    SYNC["sync/"]
    ORCH_ROLES["orchestration/roles"]
    ORCH_WORKFLOW["orchestration/workflow"]
    ORCH_LOCK["orchestration/locking"]
    ORCH_HANDOFF["orchestration/handoff"]
    ORCH_MATCH["orchestration/matching"]
    ORCH_CR["orchestration/conflict_res"]
    ORCH_OBS["orchestration/observability"]
    CLI["cli/"]
    MCP["mcp/"]
    LSP["lsp/"]
    WEB["web/"]
    GIT["git/"]
    GITHUB["github/"]

    MODELS --> ERRORS
    PARSER --> MODELS
    SERIALIZER --> MODELS
    INGEST --> PARSER
    INGEST --> DB_REPO
    DB_REPO --> MODELS
    DB_REPO --> DB_CONN
    DB_MIG --> MIGRATIONS

    GRAPH_SUB --> DB_REPO
    GRAPH_HEALTH --> DB_REPO
    GRAPH_MERMAID --> DB_REPO
    EMBED --> DB_REPO
    TASK --> DB_REPO
    TASK --> GRAPH_SUB
    TASK --> EMBED
    CONFLICT --> DB_REPO
    SYNC --> DB_REPO
    SYNC --> SERIALIZER
    SYNC --> INGEST

    ORCH_WORKFLOW --> TASK
    ORCH_WORKFLOW --> DB_REPO
    ORCH_LOCK --> DB_REPO
    ORCH_HANDOFF --> TASK
    ORCH_MATCH --> DB_REPO
    ORCH_CR --> DB_REPO
    ORCH_OBS --> DB_REPO

    CLI --> INGEST
    CLI --> GRAPH_SUB
    CLI --> EMBED
    CLI --> TASK
    CLI --> CONFLICT
    CLI --> SYNC
    CLI --> GIT
    CLI --> ORCH_WORKFLOW

    MCP --> INGEST
    MCP --> GRAPH_SUB
    MCP --> EMBED
    MCP --> TASK
    MCP --> ORCH_WORKFLOW

    LSP --> PARSER

    WEB --> DB_REPO
    WEB --> GRAPH_SUB
    WEB --> TASK

    GITHUB --> GIT
    GITHUB --> SYNC
```

---

## 4. Database Schema

KGN uses PostgreSQL with the pgvector extension. All tables are managed through sequential SQL migrations (`001`–`009`).

Key design decisions:
- **`nodes`** stores all knowledge graph nodes with a polymorphic `type` column.
- **`node_embeddings`** uses pgvector's `vector(1536)` type with an HNSW index for fast cosine similarity search.
- **`node_versions`** auto-captures a snapshot before every update (audit trail).
- **`agent_activities`** is INSERT-only — never updated or deleted (Rule R5).
- **`task_queue`** implements a state machine with lease-based checkout.

```mermaid
erDiagram
    projects ||--o{ nodes : contains
    projects {
        uuid id PK
        string name
        timestamp created_at
    }

    agents ||--o{ nodes : creates
    agents {
        uuid id PK
        string key UK
        AgentRole role
        timestamp created_at
    }

    nodes ||--o{ node_versions : "version history"
    nodes ||--o| node_embeddings : "has embedding"
    nodes ||--o{ edges : "from"
    nodes ||--o{ edges : "to"
    nodes ||--o{ task_queue : "is task"
    nodes {
        uuid id PK
        uuid project_id FK
        NodeType type
        NodeStatus status
        string title
        text body_md
        string[] tags
        float confidence
        string content_hash
        uuid created_by FK
        uuid lock_holder FK
        timestamp lock_expires
        timestamp created_at
        timestamp updated_at
    }

    node_versions {
        uuid id PK
        uuid node_id FK
        int version_num
        jsonb snapshot
        timestamp created_at
    }

    node_embeddings {
        uuid node_id PK_FK
        vector_1536 embedding
        timestamp created_at
    }

    edges {
        uuid id PK
        uuid from_id FK
        uuid to_id FK
        EdgeType type
        float weight
        jsonb properties
    }

    task_queue {
        uuid id PK
        uuid task_node_id FK
        TaskState state
        int priority
        uuid agent_id FK
        timestamp lease_expires
        int attempt
        string reason
    }

    kgn_ingest_log {
        uuid id PK
        string operation
        uuid node_id FK
        string result
        timestamp created_at
    }

    agent_activities {
        uuid id PK
        uuid agent_id FK
        ActivityType type
        uuid target_node_id FK
        jsonb metadata
        timestamp created_at
    }
```

---

## 5. Ingest Pipeline

The ingest pipeline transforms raw `.kgn`/`.kge` text into database records. It handles ID resolution (converting `new:slug` to UUIDs), content-hash deduplication, and project binding in a single pass.

```mermaid
flowchart LR
    A[".kgn / .kge<br/>File"] --> B{"File type?"}
    B -->|.kgn| C["parse_kgn_text()"]
    B -->|.kge| D["parse_kge_text()"]

    C --> E["ParsedNode<br/>(Pydantic)"]
    D --> F["list of EdgeRecord"]

    E --> G{"content_hash<br/>exists?"}
    G -->|Yes| H["SKIPPED"]
    G -->|No| I["ID Resolution"]

    I --> J["IngestService<br/>project binding"]
    J --> K["KgnRepository<br/>upsert_node()"]
    K --> L[("PostgreSQL<br/>nodes table")]

    F --> M["Edge Validation"]
    M --> N["KgnRepository<br/>insert_edge()"]
    N --> O[("PostgreSQL<br/>edges table")]

    K --> P["kgn_ingest_log"]
    N --> P

    style H fill:#ffa,stroke:#333
    style L fill:#bfb,stroke:#333
    style O fill:#bfb,stroke:#333
```

---

## 6. Task Lifecycle

Tasks follow a state machine pattern with lease-based checkout. When an agent checks out a task, a lease timer starts. If the lease expires before completion, the task is automatically recovered to READY state.

```mermaid
stateDiagram-v2
    [*] --> READY : enqueue_task()
    READY --> IN_PROGRESS : task_checkout()
    READY --> BLOCKED : dependency not met
    BLOCKED --> READY : dependency completed
    IN_PROGRESS --> DONE : task_complete()
    IN_PROGRESS --> FAILED : task_fail()
    IN_PROGRESS --> READY : lease expired (auto-recovery)
    DONE --> [*]
    FAILED --> [*]

    note right of IN_PROGRESS
        Lease-based lock
        Auto-expires on timeout
    end note

    note right of BLOCKED
        Auto-unblocked when
        DEPENDS_ON targets
        reach DONE state
    end note
```

---

## 7. Task Checkout — Context Package

When an AI agent calls `task_checkout`, KGN doesn't just return the task — it builds a **ContextPackage** containing the task details, its surrounding subgraph (2-hop BFS), and semantically similar nodes. This gives the agent everything it needs to work without extra queries.

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant TaskSvc as TaskService
    participant Repo as KgnRepository
    participant SubSvc as SubgraphService
    participant EmbedSvc as EmbeddingService

    Agent->>MCP: task_checkout(project, agent)
    MCP->>TaskSvc: checkout(project, agent)
    TaskSvc->>Repo: get highest-priority READY task
    Repo-->>TaskSvc: TaskQueueItem
    TaskSvc->>Repo: acquire lease (set lock_expires)

    TaskSvc->>SubSvc: extract(task_node_id, depth=2)
    SubSvc->>Repo: BFS traversal (nodes + edges)
    Repo-->>SubSvc: SubgraphResult

    TaskSvc->>Repo: search_similar_nodes(task_node_id, top_k=5)
    Repo-->>TaskSvc: list[SimilarNode]

    TaskSvc-->>MCP: ContextPackage
    MCP-->>Agent: ContextPackage JSON

    Note over Agent: Agent works on task...

    Agent->>MCP: task_complete(task_id)
    MCP->>TaskSvc: complete(task_id)
    TaskSvc->>Repo: state → DONE
    TaskSvc->>Repo: unblock dependent tasks
```

---

## 8. Embedding & Similarity Search

KGN uses OpenAI's `text-embedding-3-small` model to generate 1536-dimensional vectors from node body text. These vectors are stored in pgvector with an HNSW index, enabling fast cosine similarity search across the entire knowledge graph.

```mermaid
flowchart TB
    subgraph "Embed"
        A["Node body_md"] --> B["EmbeddingService"]
        B --> C["EmbeddingClient<br/>(Protocol)"]
        C --> D["OpenAI API<br/>text-embedding-3-small"]
        D --> E["1536-dim vector"]
        E --> F["KgnRepository<br/>store_embedding()"]
        F --> G[("node_embeddings<br/>pgvector HNSW")]
    end

    subgraph "Search"
        H["query_similar(node_id, k)"] --> I["KgnRepository<br/>search_similar_nodes()"]
        I --> G
        G --> J["Cosine Distance<br/>HNSW Index Scan"]
        J --> K["Top-K Similar Nodes"]
    end
```

---

## 9. Interface Layer

KGN exposes the same service layer through four independent interfaces. Each interface is a thin adapter — no business logic lives in the interface layer.

| Interface | Framework | Transport | Use Case |
|---|---|---|---|
| **CLI** | Typer + Rich | Terminal | Developer workflows, scripting, CI/CD |
| **MCP Server** | FastMCP | stdio / SSE / HTTP | AI agent integration (Claude) |
| **LSP Server** | pygls | stdio | IDE support (VS Code) |
| **Web Dashboard** | FastAPI + Jinja2 | HTTP | Visual exploration, monitoring |

```mermaid
graph LR
    subgraph "CLI (Typer)"
        CLI_INIT["kgn init"]
        CLI_INGEST["kgn ingest"]
        CLI_QUERY["kgn query"]
        CLI_TASK["kgn task"]
        CLI_EMBED["kgn embed"]
        CLI_SYNC["kgn sync"]
        CLI_GIT["kgn git"]
        CLI_GRAPH["kgn graph"]
    end

    subgraph "MCP Server (FastMCP)"
        MCP_READ["get_node<br/>query_nodes<br/>get_subgraph<br/>query_similar"]
        MCP_WRITE["ingest_node<br/>ingest_edge"]
        MCP_TASK["task_checkout<br/>task_complete<br/>task_fail"]
        MCP_WF["workflow_list<br/>workflow_run"]
    end

    subgraph "LSP Server (pygls)"
        LSP_DIAG["Diagnostics"]
        LSP_COMP["Completion"]
        LSP_HOVER["Hover"]
        LSP_LENS["CodeLens"]
        LSP_TOK["Semantic Tokens"]
    end

    subgraph "Web Dashboard (FastAPI)"
        WEB_API["REST API<br/>/api/v1/*"]
        WEB_UI["Jinja2 SPA"]
    end

    SERVICES["Service Layer"]

    CLI_INIT --> SERVICES
    CLI_INGEST --> SERVICES
    CLI_QUERY --> SERVICES
    CLI_TASK --> SERVICES
    CLI_EMBED --> SERVICES
    CLI_SYNC --> SERVICES
    CLI_GIT --> SERVICES
    CLI_GRAPH --> SERVICES

    MCP_READ --> SERVICES
    MCP_WRITE --> SERVICES
    MCP_TASK --> SERVICES
    MCP_WF --> SERVICES

    WEB_API --> SERVICES
    WEB_UI --> SERVICES

    LSP_DIAG --> SERVICES
```

---

## 10. MCP Server Tools

The MCP server provides 12 tools across 4 categories that AI agents can call. All tools delegate to the service layer — MCP handlers contain no business logic (Rule R12).

```mermaid
graph TB
    subgraph "MCP Server"
        direction TB
        SERVER["FastMCP<br/>stdio transport"]

        subgraph "Read Tools"
            R1["get_node(node_id)"]
            R2["query_nodes(project, type?, status?)"]
            R3["get_subgraph(node_id, depth?)"]
            R4["query_similar(node_id, top_k?)"]
        end

        subgraph "Write Tools"
            W1["ingest_node(kgn_content)"]
            W2["ingest_edge(kge_content)"]
        end

        subgraph "Task Tools"
            T1["task_checkout(project, agent)"]
            T2["task_complete(task_id)"]
            T3["task_fail(task_id, reason)"]
        end

        subgraph "Workflow Tools"
            WF1["workflow_list()"]
            WF2["workflow_run(template, args)"]
        end
    end

    CLAUDE["Claude / AI Agent"] <-->|stdio| SERVER
    SERVER --> R1 & R2 & R3 & R4
    SERVER --> W1 & W2
    SERVER --> T1 & T2 & T3
    SERVER --> WF1 & WF2
```

---

## 11. LSP Server Capabilities

The Language Server provides IDE-level support for `.kgn` and `.kge` files. It uses the tolerant parser (`parse_kgn_tolerant`) which never throws exceptions, ensuring real-time diagnostics work even on incomplete or malformed files.

The workspace indexer maintains an in-memory O(1) lookup table for all nodes and edges, enabling instant completions and hover info.

```mermaid
graph TB
    VSCODE["VS Code + Extension"] <-->|stdio| LSP["pygls LSP Server"]

    LSP --> DIAG["Diagnostics<br/>Real-time .kgn/.kge validation"]
    LSP --> COMP["Completion<br/>Types, Statuses, Node IDs"]
    LSP --> HOVER["Hover<br/>UUID / Slug / Enum info"]
    LSP --> LENS["CodeLens<br/>Reference counts, Edge summaries"]
    LSP --> TOKENS["Semantic Tokens<br/>Frontmatter highlighting"]
    LSP --> CUSTOM["Custom: kgn/subgraph<br/>Inline subgraph view"]

    IDX["Indexer<br/>O(1) workspace lookup"] --> COMP
    IDX --> HOVER
    IDX --> LENS

    PARSER["Tolerant Parser<br/>(never throws)"] --> DIAG
    PARSER --> TOKENS
```

---

## 12. Orchestration Layer

The orchestration layer coordinates multiple AI agents working on the same knowledge graph. It enforces role-based permissions, manages concurrent access through lease-based locking, and propagates context between sequential tasks.

| Component | Responsibility |
|---|---|
| **RoleGuard** | Permission enforcement per AgentRole (genesis / worker / reviewer / indexer / admin) |
| **WorkflowEngine** | Declarative task decomposition — templates define step sequences and dependencies |
| **NodeLockService** | Lease-based pessimistic locking to prevent concurrent modifications |
| **HandoffService** | Context propagation between sequential workflow steps |
| **MatchingService** | Assigns tasks to agents based on role compatibility and current load |
| **ConflictResolutionService** | Detects concurrent edits and mediates resolution |
| **ObservabilityService** | Tracks agent activities, measures throughput, detects bottlenecks |

```mermaid
graph TB
    subgraph "Orchestration"
        ROLES["RoleGuard<br/>Permission enforcement"]
        WF["WorkflowEngine<br/>Declarative task decomposition"]
        LOCK["NodeLockService<br/>Lease-based locking"]
        HANDOFF["HandoffService<br/>Context propagation"]
        MATCH["MatchingService<br/>Agent-task assignment"]
        CR["ConflictResolutionService<br/>Concurrent edit detection"]
        OBS["ObservabilityService<br/>Activity tracking & bottlenecks"]
    end

    AGENT_A["Agent A<br/>(worker)"] --> MATCH
    AGENT_B["Agent B<br/>(reviewer)"] --> MATCH
    AGENT_C["Agent C<br/>(indexer)"] --> MATCH

    MATCH --> ROLES
    ROLES -->|allowed?| WF

    WF -->|create tasks| TASK_QUEUE["Task Queue"]
    TASK_QUEUE --> LOCK
    LOCK -->|acquired| HANDOFF
    HANDOFF -->|context| AGENT_A

    AGENT_A -->|edits node| CR
    AGENT_B -->|edits same node| CR
    CR -->|conflict detected| RESOLUTION["Resolution<br/>auto-merge / latest-wins / manual"]

    AGENT_A --> OBS
    AGENT_B --> OBS
    OBS --> STATS["AgentStats<br/>Throughput<br/>Bottleneck Detection"]
```

---

## 13. Sync & GitHub Integration

KGN treats PostgreSQL as the local working engine and GitHub as the long-term source of truth. The sync layer handles bidirectional conversion between DB records and `.kgn`/`.kge` files, with Git providing version control and GitHub providing remote storage and collaboration.

```mermaid
flowchart TB
    subgraph "Export (DB → Files)"
        DB1[("PostgreSQL")] --> REPO1["KgnRepository<br/>query_nodes()"]
        REPO1 --> SER["Serializer<br/>serialize_node()"]
        SER --> FILES[".kgn / .kge<br/>files on disk"]
    end

    subgraph "Git Layer"
        FILES --> GIT_ADD["git add"]
        GIT_ADD --> GIT_COMMIT["git commit"]
        GIT_COMMIT --> GIT_PUSH["git push"]
    end

    subgraph "GitHub"
        GIT_PUSH --> REMOTE["GitHub Repository"]
        REMOTE --> PR["Auto PR<br/>(PullRequestService)"]
    end

    subgraph "Import (Files → DB)"
        REMOTE --> GIT_PULL["git pull"]
        GIT_PULL --> FILES2[".kgn / .kge<br/>files on disk"]
        FILES2 --> PARSE["Parser"]
        PARSE --> ING["IngestService"]
        ING --> DB2[("PostgreSQL")]
    end

    subgraph "Conflict Handling"
        GIT_PULL --> DETECT["ConflictDetector"]
        DETECT -->|db-wins| DB2
        DETECT -->|file-wins| FILES2
        DETECT -->|manual| MANUAL["Manual Resolution"]
    end
```

---

## 14. Conflict Detection

KGN detects potential knowledge conflicts by comparing node embeddings via cosine similarity. When two nodes exceed the similarity threshold (default 0.92), they are flagged as conflict candidates. Optionally, a `CONTRADICTS` edge is auto-created to make the conflict visible in the graph.

```mermaid
flowchart LR
    A["ConflictService<br/>scan(project, threshold=0.92)"] --> B["KgnRepository<br/>find_conflict_candidates()"]
    B --> C[("node_embeddings<br/>pgvector")]
    C --> D["Pairwise Cosine<br/>Similarity"]
    D --> E{"similarity<br/>> threshold?"}
    E -->|Yes| F["ConflictCandidate<br/>(node_a, node_b, score)"]
    E -->|No| G["No conflict"]
    F --> H["Create CONTRADICTS<br/>edge (optional)"]
```

---

## 15. Workflow Engine

The workflow engine decomposes high-level processes into task DAGs. Each `WorkflowTemplate` defines a sequence of steps with dependency edges. When executed, the engine creates TASK nodes, wires them with `DEPENDS_ON` edges, and enqueues them in priority order. Downstream tasks start as BLOCKED and auto-transition to READY as their dependencies complete.

Built-in templates:

| Template | Pipeline | Description |
|---|---|---|
| `design-to-impl` | GOAL → SPEC → ARCH → TASK(impl) → TASK(review) | Full design-to-implementation |
| `issue-resolution` | ISSUE → TASK(fix) → TASK(verify) | Bug fix workflow |
| `knowledge-indexing` | GOAL → TASK(index) → TASK(review) | Knowledge capture |

```mermaid
flowchart TB
    TEMPLATE["WorkflowTemplate<br/>(declarative DAG)"] --> ENGINE["WorkflowEngine"]

    ENGINE --> S1["Step 1: Create TASK node"]
    ENGINE --> S2["Step 2: Create TASK node"]
    ENGINE --> S3["Step 3: Create TASK node"]

    S1 --> E1["DEPENDS_ON edge"]
    E1 --> S2
    S2 --> E2["DEPENDS_ON edge"]
    E2 --> S3

    S1 --> Q1["Enqueue (priority=high)"]
    S2 --> Q2["Enqueue (BLOCKED)"]
    S3 --> Q3["Enqueue (BLOCKED)"]

    Q1 --> TQ["task_queue"]
    Q2 --> TQ
    Q3 --> TQ

    TQ --> CHECKOUT["Agent checkout<br/>→ Step 1 first"]
```

---

## 16. End-to-End Data Flow

The complete picture — from input sources through processing and storage to output interfaces. This diagram shows how all layers connect in a running KGN system.

```mermaid
flowchart TB
    subgraph "Input"
        FILES[".kgn / .kge files"]
        AGENT_INPUT["AI Agent<br/>(via MCP)"]
        WEB_INPUT["Web Dashboard"]
        CLI_INPUT["CLI commands"]
    end

    subgraph "Processing"
        PARSER["Parser"]
        INGEST["IngestService"]
        EMBED_SVC["EmbeddingService"]
        OPENAI["OpenAI API"]
    end

    subgraph "Storage"
        PG[("PostgreSQL")]
        PGVEC[("pgvector<br/>HNSW")]
    end

    subgraph "Query & Analysis"
        SUBGRAPH["SubgraphService<br/>BFS extraction"]
        SIMILAR["Similarity Search<br/>cosine distance"]
        CONFLICT["ConflictService"]
        HEALTH["HealthService"]
        MERMAID_GEN["MermaidGenerator"]
    end

    subgraph "Task Management"
        TASK_SVC["TaskService"]
        WF_ENGINE["WorkflowEngine"]
        ORCHESTRATION["Orchestration<br/>Roles · Locks · Handoff"]
    end

    subgraph "Output"
        CTX_PKG["ContextPackage<br/>(for AI agents)"]
        SYNC_OUT["Sync → Git → GitHub"]
        VIZ["Mermaid / Web View"]
        LSP_OUT["LSP → VS Code"]
    end

    FILES --> PARSER --> INGEST --> PG
    AGENT_INPUT --> INGEST
    CLI_INPUT --> INGEST

    INGEST --> EMBED_SVC --> OPENAI --> PGVEC

    PG --> SUBGRAPH --> CTX_PKG
    PGVEC --> SIMILAR --> CTX_PKG
    PG --> CONFLICT
    PG --> HEALTH
    PG --> MERMAID_GEN --> VIZ

    TASK_SVC --> CTX_PKG
    WF_ENGINE --> TASK_SVC
    ORCHESTRATION --> TASK_SVC

    PG --> SYNC_OUT
    PARSER --> LSP_OUT

    CTX_PKG --> AGENT_INPUT
```

---

## Design Rules

| Rule | Description |
|---|---|
| **R1** | ALL SQL lives in `KgnRepository` — no SQL in services or handlers |
| **R5** | `agent_activities` table is INSERT-only (audit log) |
| **R8** | All embedding API calls go through `EmbeddingClient` Protocol |
| **R10** | Task state transitions only via `TaskService` / `KgnRepository` |
| **R12** | MCP handlers contain no business logic — delegate to services |
| **R16** | Agent without role defaults to `admin` (full access) |
| **R23** | LSP: blocking work must use `asyncio.to_thread()` |
| **R24** | `parse_kgn_tolerant()` never raises exceptions |
| **V7** | Node upsert validates `supersedes` target exists |
| **V8** | Content-hash deduplication: same hash → SKIPPED |
