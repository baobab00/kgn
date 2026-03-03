# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.11.0]

Phase 11 — VS Code Extension + Language Server

### Added

- **VS Code Extension** (`vscode-kgn`):
  - TextMate grammar for `.kgn` and `.kge` files (YAML front matter + Markdown body)
  - Language configuration (bracket matching, auto-closing, comment toggling)
  - Snippets for node and edge templates
  - `KGN: Show Graph Preview` command
  - Settings: `kgn.pythonPath`, `kgn.lsp.enabled`, `kgn.trace.server`
  - Auto-detection of Python interpreter with `kgn[lsp]` installed
- **Language Server Protocol (LSP)** (`kgn lsp serve`):
  - `textDocument/diagnostic` — Real-time validation (V1–V10 rules) via `parse_kgn_tolerant`
  - `textDocument/completion` — Enum auto-completion for `type`, `status`, edge types, `kgn_version`
  - `textDocument/hover` — Node ID resolution, field descriptions
  - `textDocument/definition` — Go to Definition for referenced node UUIDs
  - `textDocument/codeLens` — Reference counts per node
  - `textDocument/semanticTokens` — Enhanced highlighting (enum values, UUIDs, slugs, dates)
  - Subgraph preview panel (Mermaid rendering)
  - Workspace file indexer (background `.kgn`/`.kge` scanning)
  - UTF-16 ↔ UTF-8 position adapter for correct cursor positioning
  - Debounced diagnostics (300ms) for responsive editing
- **Error Recovery Parser** (`parse_kgn_tolerant`):
  - Partial parse results on malformed input (best-effort front matter + diagnostics)
  - Line-level error reporting with LSP Diagnostic ranges
- **CLI** — `kgn lsp serve` command (stdio transport for VS Code)
- **E2E Integration Tests** — 19 LSP test scenarios (diagnostics, completion, hover, definition, CodeLens, semantic tokens)
- **Python Interpreter Resolution** — Multi-strategy detection (.venv, PATH, `kgn.pythonPath` setting)

### Changed

- `parse_kgn` — Internal validator now produces structured `ParseDiagnostic` objects
- `kgn/__init__.py` — `__version__`: 0.10.0 → 0.11.0
- All source code, SQL migrations, CLI help — English only (i18n cleanup)
- README: VS Code Extension section added, CLI table updated, test count updated (1516+ → 2000+)

## [0.10.0]

Phase 10 — Multi-Agent Orchestration

### Added

- Web UI Design System (`kgn/web/static/style.css`):
  - CSS Custom Properties (`:root` tokens) — brand, neutral, semantic, surface, shadow, transition, typography
  - Flex-based viewport layout (fixes bottom cutoff by taskbar)
  - Agents timeline scroll stabilization (`calc(100vh - 180px)` + `overflow-y: auto`)
  - Design token adoption across 7 CSS files (graph, agents, dashboard, kanban, detail, search)
  - Hover lift animation, custom scrollbar, responsive breakpoint

- Agent Role system (`kgn/orchestration/roles.py`):
  - 5 roles: genesis, worker, reviewer, indexer, admin
  - `RoleGuard` — node/edge creation + task checkout permission enforcement
  - MCP tool-level role guard integration (ingest_node, ingest_edge, task_checkout)
- WorkflowEngine (`kgn/orchestration/workflow.py`):
  - Template-driven multi-step workflow execution
  - 3 built-in templates: design-to-impl, issue-resolution, knowledge-indexing
  - Auto-creates nodes + dependency edges + enqueues TASK nodes
  - MCP `workflow_run` tool
- Agent Handoff Protocol (`kgn/orchestration/handoff.py`):
  - `HandoffService.propagate_context()` — injects predecessor context into successor tasks
  - `MatchingService` — role-based task-to-agent matching
  - Role-filtered `task_checkout` (worker sees worker tasks, reviewer sees reviewer tasks)
  - Automatic handoff on `task_complete`
- Concurrent Access Guard (`kgn/orchestration/locking.py`):
  - `NodeLockService` — advisory node locking with expiry
  - Auto-lock on task checkout, auto-release on complete/fail
  - Expired lock recovery (reacquirable after expiry)
- Conflict Resolution (`kgn/orchestration/conflict_resolution.py`):
  - `ConflictResolutionService` — detects multi-agent node modifications
  - Auto-creates ISSUE node + reviewer TASK for conflict review
  - Integration with IngestService upsert pipeline
- Observability & Workflow Tracking (`kgn/orchestration/observability.py`):
  - `ObservabilityService` — agent stats, activity timeline, task flow, bottleneck detection
  - `kgn agent list/role/stats/timeline` CLI commands
  - 5 Web API routes (`/agents`, `/agents/{id}/stats`, `/agents/{id}/timeline`, etc.)
- Web UI Dashboard Integration:
  - Agents tab with stats + timeline views (`agents.js`, `agents.css`)
  - Kanban board enhanced with agent_key + role badge
  - Dashboard charts with agent performance metrics
- E2E Multi-Agent Scenario Verification (`tests/test_e2e_phase10.py`):
  - 6 scenario classes, 24 E2E tests
  - TestMultiAgentWorkflow, TestRoleBasedAccess, TestHandoffChain,
    TestConcurrentAccess, TestConflictResolution, TestObservability
- Migration 007: `agent_role_enum` type + agents.agent_role column

### Changed

- `conflict_resolution.py` — Removed `_save_version()`/`_update_node()` private method calls → uses public `upsert_node()` (R1 compliance)
- `conflict_resolution.py` — Replaced direct `repo.enqueue_task()` call → uses `TaskService.enqueue()` (R10 compliance)
- `workflow.py` — Added `_parse_uuid()` None check, removed unused `KgnError` import
- `task_checkout` — supports `role_filter` parameter for role-based task routing
- `IngestService.ingest_node` — conflict detection on node update (multi-agent)
- `TaskService.complete` — auto-handoff context propagation to dependents
- README: Multi-Agent Orchestration section added, CLI table updated
- Tech Stack: test count updated (1168+ → 1516+)
- `__version__`: 0.9.0 → 0.10.0

## [0.9.0]

Phase 9 — Web Visualization & Dashboard

### Added

- `kgn web serve` CLI command — FastAPI-based web dashboard server
- FastAPI REST API (7 read-only routes under `/api/v1/`):
  - `GET /nodes`, `GET /nodes/{id}` — node listing with type/status/tags/text filters
  - `GET /subgraph/{id}` — k-hop BFS subgraph (Cytoscape.js format, depth 1-5, max 200 nodes)
  - `GET /edges` — incoming/outgoing edges with peer titles
  - `GET /health` — graph health metrics (HealthService)
  - `GET /tasks`, `GET /tasks/{id}`, `GET /tasks/{id}/activities` — task queue listing
  - `GET /stats` — aggregated statistics + Health Index
  - `GET /similar/{id}` — cosine similarity search
  - `GET /conflicts` — conflict candidate listing
  - `GET /events` — Server-Sent Events (SSE) stream infrastructure
- Cytoscape.js interactive graph visualization (dagre layout)
- Node detail panel (front matter + Markdown body + edge list)
- Task Kanban board (READY / IN_PROGRESS / BLOCKED / DONE / FAILED)
- Health Dashboard with Chart.js (node types, edge types, task pipeline, health index gauge)
- Search & filter bar (type/status/tags/text), similar node highlight, conflict overlay
- SSE EventBus infrastructure (publish/subscribe, 100-event history)
- Optional `[web]` dependency group: `fastapi>=0.115`, `uvicorn[standard]>=0.34`, `jinja2>=3.1`
- E2E test suite for all web routes (`test_e2e_phase9.py`)
- 125+ web unit tests across 6 test files

### Changed

- README: Web Dashboard section added, CLI table updated
- Tech Stack: test count updated (1043 → 1168+)
- `__version__`: 0.8.0 → 0.9.0

## [0.8.0]

Phase 8 — Distribution & Packaging

### Added

- `pip install kgn` support — PyPI distribution ready
- `docker/Dockerfile` — multi-stage build (builder → runtime), non-root execution
- `docker-compose.yml` — PostgreSQL + kgn CLI all-in-one Compose
- `.github/workflows/publish.yml` — PyPI Trusted Publisher (OIDC) automated deployment
- `docs/SPEC/RELEASE_CHECKLIST.md` — release procedure + TestPyPI guide
- `.dockerignore` — build context optimization
- This CHANGELOG file

### Changed

- `migrations/` → `kgn/migrations/` moved inside package (pip install compatibility)
- `MIGRATIONS_DIR` path fix (`parent.parent / "migrations"`)
- pyproject.toml: `dynamic = ["version"]` + hatchling single source of truth (`kgn/__init__.py`)
- pyproject.toml: classifiers (13), urls (4), keywords (6), authors added
- CI `fail_under`: 80% → 90% sync
- CI `build` job added (SQL file verification in wheel)
- README: `pip install kgn` install guide + Docker all-in-one section
- `__version__`: 0.6.0 → 0.8.0 consistency fix

### Fixed

- pyproject.toml TOML section ordering error (`dependencies` inside `[project.urls]`)
- Missing `LICENSE` file (MIT)

## [0.7.0]

Phase 7 — Quality Excellence

### Added

- Stress/concurrency tests: 100/200 node round-trip, 4 concurrency scenarios
- 16 new test files (CLI, sync, github, write_error_paths, etc.)

### Changed

- **Coverage 82% → 97%** (all modules 80%+, 0 modules below 80%)
- Tests 769 → 1019 (+250)
- CI quality gate `fail_under = 85`
- `detect_default_branch` 4-step fallback strategy
- Test infrastructure modernization: `helpers.py` consolidation, conftest cleanup

### Fixed

- OperationalError priority handling refinement
- KgnError escalation prevention
- Merge abort cleanup logic improvement

## [0.6.0]

Phase 6 — Git/GitHub Integration

### Added

- Serializer (`serializer/`): NodeRecord/EdgeRecord ↔ .kgn/.kge round-trip
- Export/Import service (`sync/`): DB ↔ filesystem bidirectional sync
- BLOCKED dependency chain (`task/dependency.py`): DFS cycle detection, automatic unblock
- Git integration (`git/`): init, commit, status, log, push, pull
- GitHub sync (`github/`): REST API v3, push/pull, conflict detection
- Branch/PR workflow: `kgn/<task-id>` pattern branches, PR create/review/merge
- Mermaid visualization: graph flowchart, task board Kanban, README auto-generation
- 18 new CLI commands (39 total)
- Dependency: `httpx>=0.28`

### Changed

- N+1 query batch optimization
- Large graph node limit (`max_nodes=200`)

### Fixed

- DB error reclassification (OperationalError vs KgnError)

## [0.5.0]

Phase 5 — Production Readiness

### Added

- Embedding factory: environment-variable-based client auto-selection
- MCP `ingest_node` automatic embedding (graceful skip)
- CI/CD pipeline (`.github/workflows/ci.yml`): lint → test → coverage gate
- Structured logging (`structlog`): JSON/Console + stderr separation
- Error code system (`KgnErrorCode`): 20 codes, 4-field JSON response
- `safe_tool_call` decorator: applied to all MCP tools
- Connection pool hardening: timeout/idle/reconnect environment variables
- `kgn embed provider test` CLI
- Dependency: `structlog>=24.0`

### Changed

- Total CLI commands: 21

### Fixed

- DB PoolTimeout/OperationalError auto-capture + error code return

## [0.4.0]

Phase 4 — MCP Server

### Added

- MCP server (`mcp/`): FastMCP-based 10 MCP tools
  - Read: `get_node`, `query_nodes`, `get_subgraph`, `query_similar`
  - Task: `task_checkout`, `task_complete`, `task_fail`
  - Write: `ingest_node`, `ingest_edge`, `enqueue_task`
- `kgn mcp serve` CLI (stdio/sse/streamable-http)
- Claude Desktop/Code direct integration (`CLAUDE.md`, config)
- `requeue_expired` automation: expired lease recovery on checkout
- `IngestService.ingest_text()`: direct string ingest

### Changed

- MCP server.py → 5-file refactor (server + helpers + tools)
- Total CLI commands: 20

### Fixed

- `__version__` 0.4.0 correction
- EmbeddingService R1 compliance fix

## [0.3.0]

Phase 3 — Task Orchestration

### Added

- Task orchestration (`task/`): SKIP LOCKED queue
- `TaskService`: enqueue, checkout, complete, fail, list
- `ContextPackage`: structured context package for AI agents
- `HandoffFormatter`: JSON/Markdown handoff output
- 6 new CLI commands: `kgn task enqueue/checkout/complete/fail/list/log`
- `task_queue` table, `task_state` ENUM
- Automatic `agent_activities` recording on checkout/complete/fail
- Migration 006

### Changed

- `kgn health` WIP metric: `task_queue IN_PROGRESS` based
- Total CLI commands: 19

## [0.2.0]

Phase 2 — Embeddings & Conflict Detection

### Added

- Embedding service (`embedding/`): OpenAI embeddings + pgvector Top-K similarity search
- Conflict detection service (`conflict/`): cosine similarity-based automatic conflict detection
- New CLI: `kgn embed`, `kgn query similar`, `kgn conflict scan/approve/dismiss`
- `kgn ingest --embed` option
- `node_embeddings` table (vector(1536) + HNSW index)
- `edges.status` column (PENDING/APPROVED/DISMISSED)
- DupSpecRate health metric
- Migration 004–005

### Fixed

- R1 violation resolution (no direct SQL outside Repository)

## [0.1.0]

Phase 1 — Core Foundation

### Added

- `.kgn` / `.kge` file parser (YAML front matter + Markdown body)
- Pydantic data models: `NodeType` (10 types), `NodeStatus` (4 statuses), `EdgeType` (7 types)
- PostgreSQL DB schema + migration runner (migration 001–003)
- `KgnRepository`: sole SQL access layer (R1)
- Validation rules V1–V10
- `IngestService`: slug resolution, conflict detection, FK protection
- `SubgraphService`: BFS subgraph extraction (JSON/Markdown)
- `HealthService`: 5 health metrics
- Typer CLI 6 commands: `init`, `ingest`, `status`, `query`, `health`
- DB tables: `projects`, `agents`, `nodes`, `edges`, `node_versions`, `kgn_ingest_log`, `agent_activities`
- SAVEPOINT-based test isolation

[Unreleased]: https://github.com/baobab00/kgn/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/baobab00/kgn/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/baobab00/kgn/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/baobab00/kgn/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/baobab00/kgn/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/baobab00/kgn/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/baobab00/kgn/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/baobab00/kgn/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/baobab00/kgn/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/baobab00/kgn/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/baobab00/kgn/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/baobab00/kgn/releases/tag/v0.1.0
