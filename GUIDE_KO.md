# KGN — 시작 가이드

> AI 에이전트에게 공유 지식 그래프로 장기 기억을 부여하세요.  
> 이 가이드를 따라하면 KGN 설치부터 첫 지식 등록, Claude 연동까지 10분 안에 완료할 수 있습니다.

---

## 목차

- [KGN이 뭔가요?](#kgn이-뭔가요)
- [사전 준비](#사전-준비)
- [1단계: KGN 설치](#1단계-kgn-설치)
- [2단계: 데이터베이스 시작](#2단계-데이터베이스-시작)
- [3단계: 프로젝트 생성](#3단계-프로젝트-생성)
- [4단계: 첫 .kgn 파일 작성](#4단계-첫-kgn-파일-작성)
- [5단계: 등록 및 조회](#5단계-등록-및-조회)
- [6단계: Claude 연동](#6단계-claude-연동)
- [더 알아보기](#더-알아보기)
  - [관계 정의 (.kge 파일)](#관계-정의-kge-파일)
  - [의미 기반 검색 (임베딩)](#의미-기반-검색-임베딩)
  - [웹 대시보드](#웹-대시보드)
  - [태스크 큐 & 멀티 에이전트](#태스크-큐--멀티-에이전트)
  - [Git/GitHub 연동](#gitgithub-연동)
  - [VS Code 확장](#vs-code-확장)
- [CLI 요약 표](#cli-요약-표)
- [노드 타입 & 상태값](#노드-타입--상태값)
- [문제 해결](#문제-해결)

---

## KGN이 뭔가요?

AI 에이전트는 대화가 끝나면 모든 걸 잊어버립니다. KGN이 이 문제를 해결합니다.

KGN은 지식을 **노드**(아이디어, 스펙, 의사결정, 태스크)와 **엣지**(노드 간 관계)로 PostgreSQL 데이터베이스에 저장합니다. AI 에이전트는 MCP 서버, CLI, 웹 대시보드, LSP 에디터를 통해 이 지식을 읽고, 쓰고, 검색할 수 있습니다.

**동작 방식:**

```
.kgn 파일 작성  →  KGN이 PostgreSQL에 저장  →  AI가 언제든 조회 가능
```

---

## 사전 준비

| 필요한 것 | 왜 필요한가 | 설치 |
|---|---|---|
| **Python 3.12+** | KGN은 Python 패키지입니다 | [python.org/downloads](https://www.python.org/downloads/) |
| **Docker Desktop** | PostgreSQL을 자동으로 실행합니다 | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) |
| **Git** | Docker 설정 파일을 가져오기 위해 필요합니다 | 보통 이미 설치되어 있음 |

> **Windows 사용자:** Python 설치 시 하단의 **"Add python.exe to PATH"** 체크박스를 꼭 선택하세요.

---

## 1단계: KGN 설치

```bash
pip install kgn-mcp
```

설치 확인:

```bash
kgn --version
# kgn version 0.12.0
```

> **Tip:** `kgn` 명령어가 인식되지 않으면 [문제 해결](#kgn-명령어를-찾을-수-없음) 항목을 확인하세요.

---

## 2단계: 데이터베이스 시작

KGN은 PostgreSQL이 필요합니다. Docker로 가장 간단하게 시작할 수 있습니다:

```bash
git clone https://github.com/baobab00/kgn.git
cd kgn
docker compose -f docker/docker-compose.yml up -d postgres
```

정상 작동 확인:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
# kgn-postgres   Up ...
```

> **지금 무슨 일이 일어났나요?** Docker가 pgvector 확장이 포함된 PostgreSQL을 다운로드하고 5433 포트에서 백그라운드로 시작했습니다. 별도 창은 뜨지 않습니다.

---

## 3단계: 프로젝트 생성

```bash
kgn init --project my-project
```

이 명령은 데이터베이스 테이블을 생성(첫 실행 시)하고 프로젝트를 등록합니다. 성공 메시지가 나타나면 준비 완료입니다.

> **Tip:** `kgn init`은 `.env` 파일이 없으면 데이터베이스 접속 설정이 담긴 `.env` 파일도 자동으로 생성합니다.

---

## 4단계: 첫 .kgn 파일 작성

아무 텍스트 에디터로 `hello.kgn` 파일을 만드세요:

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

KGN을 처음 사용해 봅니다. 이 노드는 PostgreSQL에 저장되어
어떤 AI 에이전트든 나중에 찾아서 참조할 수 있습니다.

## Content

지식 노드는 메타데이터를 위한 YAML 프론트매터와 내용을 위한 마크다운으로 구성됩니다.
AI가 이해하고 검색하고 활용할 수 있는 스마트 메모라고 생각하세요.
```

**각 필드 의미:**

| 필드 | 필수 | 의미 |
|---|---|---|
| `kgn_version` | 예 | 항상 `"0.1"` |
| `id` | 예 | `"new:slug"` (새 노드, UUID 자동 생성) 또는 기존 UUID |
| `type` | 예 | 지식의 종류: GOAL, SPEC, TASK, DECISION 등 |
| `status` | 예 | 수명주기 상태: ACTIVE, DEPRECATED, SUPERSEDED, ARCHIVED |
| `title` | 예 | 사람이 읽을 수 있는 제목 |
| `project_id` | 예 | 이 노드가 속한 프로젝트 |
| `agent_id` | 예 | 만든 주체 (사용자 또는 AI 에이전트 이름) |
| `tags` | 아니오 | 필터링 및 검색용 레이블 |
| `confidence` | 아니오 | 확신도 점수 (0.0–1.0) |

---

## 5단계: 등록 및 조회

**등록** (노드를 데이터베이스에 저장):

```bash
kgn ingest hello.kgn --project my-project
```

**프로젝트 확인:**

```bash
kgn status --project my-project
```

**노드 검색:**

```bash
kgn query nodes --project my-project --type SPEC
```

**상세 건강 지표 확인:**

```bash
kgn health --project my-project
```

**제공된 예제 파일 일괄 등록:**

```bash
kgn ingest examples/ --project my-project --recursive
```

---

## 6단계: Claude 연동

여기서부터 KGN의 진가가 드러납니다 — Claude가 여러분의 지식 그래프를 직접 읽고, 쓰고, 태스크를 관리할 수 있게 됩니다.

### Claude Code (권장)

```bash
# 프로젝트 디렉토리에 .mcp.json 자동 생성:
kgn mcp init --project my-project

# 또는 Claude CLI를 직접 사용:
claude mcp add kgn -- kgn mcp serve --project my-project
```

### Claude Desktop

```bash
kgn mcp init --project my-project --target claude-desktop
```

또는 `claude_desktop_config.json`에 직접 추가:

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

Claude를 재시작하세요. 🔨 아이콘이 보이면 KGN 도구가 로드된 것입니다.

Claude는 이제 **12개 MCP 도구**에 접근할 수 있습니다:

| 도구 | 기능 |
|---|---|
| `get_node` | ID로 노드 조회 |
| `query_nodes` | 노드 검색 (타입, 상태 필터) |
| `get_subgraph` | 관련 노드 추출 (BFS 탐색) |
| `query_similar` | 의미적으로 유사한 노드 검색 (벡터 검색) |
| `ingest_node` | .kgn 텍스트로 노드 생성/수정 |
| `ingest_edge` | .kge 텍스트로 관계 생성 |
| `enqueue_task` | 태스크 큐에 추가 |
| `task_checkout` | 다음 태스크 가져오기 |
| `task_complete` | 태스크 완료 처리 |
| `task_fail` | 태스크 실패 처리 |
| `workflow_list` | 워크플로우 템플릿 목록 |
| `workflow_run` | 워크플로우 실행 (태스크 체인 생성) |

---

## 더 알아보기

### 관계 정의 (.kge 파일)

노드 간 관계를 정의할 수 있습니다. `edges.kge` 파일을 만드세요:

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

**엣지 타입:** `DEPENDS_ON`, `IMPLEMENTS`, `RESOLVES`, `SUPERSEDES`, `DERIVED_FROM`, `CONTRADICTS`, `CONSTRAINED_BY`

```bash
kgn ingest edges.kge --project my-project
```

---

### 의미 기반 검색 (임베딩)

OpenAI 임베딩을 사용해 AI 기반 유사도 검색을 활성화할 수 있습니다:

```bash
# .env에 API 키 추가
echo "KGN_OPENAI_API_KEY=sk-your-key-here" >> .env

# 연결 테스트
kgn embed provider test

# 임베딩과 함께 등록
kgn ingest hello.kgn --project my-project --embed

# 유사 노드 검색
kgn query similar <node-uuid> --project my-project --top 5
```

> API 키가 설정되지 않으면 KGN은 정상 작동하며 임베딩만 자동으로 건너뜁니다.

---

### 웹 대시보드

브라우저에서 지식 그래프를 시각적으로 확인하세요:

```bash
pip install kgn-mcp[web]
kgn web serve --project my-project --port 8080
```

http://localhost:8080 을 열면 — 인터랙티브 그래프 뷰, 노드 상세, 태스크 보드, 건강 대시보드, 검색 & 필터를 사용할 수 있습니다.

> **API 키 보호 (선택):** `KGN_API_KEY=your-secret` 환경변수를 설정하면 모든 API 엔드포인트(health 제외)에 `X-API-Key` 헤더가 필요해집니다.

---

### 태스크 큐 & 멀티 에이전트

KGN에는 여러 AI 에이전트를 조율하는 태스크 오케스트레이션 시스템이 포함되어 있습니다:

```bash
# TASK 노드를 큐에 등록
kgn task enqueue <task-node-uuid> --project my-project

# 다음 태스크 가져오기 (컨텍스트 패키지 반환)
kgn task checkout --project my-project

# 태스크 완료 또는 실패 처리
kgn task complete <task-queue-id>
kgn task fail <task-queue-id> --reason "설명"

# 전체 태스크 목록
kgn task list --project my-project
```

**워크플로우 템플릿**으로 다단계 태스크 체인을 자동으로 생성할 수 있습니다:

```bash
kgn workflow list
kgn workflow run design-to-impl --project my-project --trigger <node-uuid>
```

제공 템플릿: `design-to-impl`, `issue-resolution`, `knowledge-indexing`

**에이전트 역할**로 각 에이전트가 할 수 있는 작업을 제어합니다:

| 역할 | 생성 가능 타입 | 설명 |
|---|---|---|
| `admin` | 전체 | 전체 권한 (기본값) |
| `genesis` | GOAL, SPEC, ARCH, CONSTRAINT, ASSUMPTION | 프로젝트 설계 |
| `worker` | SPEC, ARCH, LOGIC, TASK, SUMMARY | 구현 작업 |
| `reviewer` | DECISION, ISSUE, SUMMARY | 리뷰 & 의사결정 |
| `indexer` | SUMMARY | 지식 인덱싱 |

```bash
kgn agent list --project my-project
kgn agent role --project my-project --agent-id <uuid> --role worker
```

---

### Git/GitHub 연동

KGN은 데이터베이스와 Git 간 양방향 동기화를 지원합니다:

```bash
# DB → 파일 시스템으로 내보내기
kgn sync export --project my-project --target ./sync

# 파일 시스템 → DB로 가져오기
kgn sync import --project my-project --source ./sync

# GitHub에 푸시
kgn sync push --project my-project --target ./sync

# GitHub에서 풀
kgn sync pull --project my-project --target ./sync

# Mermaid 그래프 README 생성
kgn graph mermaid --project my-project
kgn graph readme --project my-project --target ./sync
```

---

### VS Code 확장

`.kgn` 파일의 구문 강조, 진단, 자동 완성을 사용하세요:

```bash
code --install-extension baobab00.vscode-kgn
pip install kgn-mcp[lsp]    # Language Server 기능용
```

기능: 구문 강조, 실시간 유효성 검사(V1–V10 규칙), 자동 완성, 호버 정보, 정의로 이동, CodeLens, 서브그래프 미리보기 패널.

---

## CLI 요약 표

| 하고 싶은 것 | 명령어 |
|---|---|
| 프로젝트 생성 | `kgn init --project <이름>` |
| 파일 등록 | `kgn ingest <file.kgn> --project <이름>` |
| 폴더 일괄 등록 | `kgn ingest <폴더>/ --project <이름> --recursive` |
| 프로젝트 상태 확인 | `kgn status --project <이름>` |
| 그래프 건강 확인 | `kgn health --project <이름>` |
| 노드 검색 | `kgn query nodes --project <이름> [--type SPEC] [--status ACTIVE]` |
| 서브그래프 추출 | `kgn query subgraph --project <이름> --node-id <uuid>` |
| 유사도 검색 | `kgn query similar <uuid> --project <이름> --top 5` |
| 태스크 등록 | `kgn task enqueue <uuid> --project <이름>` |
| 태스크 가져오기 | `kgn task checkout --project <이름>` |
| 태스크 완료 | `kgn task complete <task-queue-id>` |
| 태스크 목록 | `kgn task list --project <이름>` |
| MCP 서버 시작 | `kgn mcp serve --project <이름> [--role admin]` |
| MCP 자동 설정 | `kgn mcp init --project <이름> [--target claude-desktop]` |
| 웹 대시보드 | `kgn web serve --project <이름> [--port 8080]` |
| 파일로 내보내기 | `kgn sync export --project <이름> --target ./sync` |
| 파일에서 가져오기 | `kgn sync import --project <이름> --source ./sync` |
| 충돌 스캔 | `kgn conflict scan --project <이름>` |
| 에이전트 목록 | `kgn agent list --project <이름>` |
| 워크플로우 실행 | `kgn workflow run <템플릿> --project <이름> --trigger <uuid>` |
| 전체 명령어 보기 | `kgn --help` |

---

## 노드 타입 & 상태값

### 타입

| 타입 | 용도 |
|---|---|
| `GOAL` | 상위 목표 |
| `ARCH` | 아키텍처 결정 또는 설계 |
| `SPEC` | 명세 또는 요구사항 |
| `LOGIC` | 구현 로직 |
| `DECISION` | 명시적 의사결정 기록 |
| `ISSUE` | 문제 또는 버그 리포트 |
| `TASK` | 실행 가능한 작업 항목 (큐에 등록 가능) |
| `CONSTRAINT` | 시스템 제약사항 |
| `ASSUMPTION` | 기록된 가정 |
| `SUMMARY` | 생성된 요약 또는 인덱스 |

### 상태

| 상태 | 의미 |
|---|---|
| `ACTIVE` | 현재 유효하며 사용 중 |
| `DEPRECATED` | 더 이상 권장되지 않음 |
| `SUPERSEDED` | 다른 노드로 대체됨 |
| `ARCHIVED` | 기록용으로 보관 |

### 엣지 타입

| 엣지 타입 | 의미 |
|---|---|
| `DEPENDS_ON` | 소스가 타겟에 의존 |
| `IMPLEMENTS` | 소스가 타겟을 구현 |
| `RESOLVES` | 소스가 타겟(이슈/충돌)을 해결 |
| `SUPERSEDES` | 소스가 타겟을 대체 |
| `DERIVED_FROM` | 소스가 타겟에서 파생 |
| `CONTRADICTS` | 소스가 타겟과 모순 |
| `CONSTRAINED_BY` | 소스가 타겟에 의해 제한 |

---

## 문제 해결

<details>
<summary><b><code>kgn</code> 명령어를 찾을 수 없음</b></summary>

Python의 Scripts 폴더가 PATH에 포함되지 않은 경우입니다.

**Windows:**
1. `Win + R`을 누르고 `sysdm.cpl`을 입력 후 Enter
2. **고급** → **환경 변수** 클릭
3. **Path**에 추가: `C:\Users\<사용자명>\AppData\Local\Programs\Python\Python312\Scripts`
4. 터미널을 재시작하세요

**macOS/Linux:**
```bash
export PATH="$HOME/.local/bin:$PATH"
# 영구 적용하려면 ~/.bashrc 또는 ~/.zshrc에 추가하세요
```

</details>

<details>
<summary><b>Docker: "Cannot connect to the Docker daemon" 에러</b></summary>

Docker Desktop이 실행되지 않은 상태입니다. Docker Desktop을 열고 작업표시줄의 고래 아이콘이 "Running" 상태가 될 때까지 기다리세요.

</details>

<details>
<summary><b>데이터베이스 연결 에러</b></summary>

1. PostgreSQL 컨테이너가 실행 중인지 확인: `docker ps`
2. 실행 중이 아니면: `docker compose -f docker/docker-compose.yml up -d postgres`
3. `.env` 파일에 올바른 접속 설정이 있는지 확인하세요 (`kgn init` 실행 시 자동 생성됨)

</details>

<details>
<summary><b>"Permission denied" 에러</b></summary>

**Windows:** 관리자 권한으로 터미널을 실행하거나, `pip install --user kgn-mcp`를 사용하세요.

**macOS/Linux:** `pip install --user kgn-mcp`를 사용하거나 `sudo`를 앞에 붙이세요.

</details>

<details>
<summary><b>MCP 서버가 Claude에 연결되지 않음</b></summary>

1. `kgn mcp serve --project <이름>` 명령이 터미널에서 단독으로 작동하는지 확인하세요
2. `claude_desktop_config.json`에 문법 오류가 없는지 확인하세요 (유효한 JSON?)
3. Claude Desktop을 완전히 재시작하세요
4. 🔨 아이콘이 안 보이면 Claude의 MCP 로그를 확인하세요

</details>

---

**도움이 더 필요하신가요?** [README](README.md)에서 상세 정보를, [Architecture Guide](ARCHITECTURE.md)에서 내부 구조를, [GitHub Issue](https://github.com/baobab00/kgn/issues)에서 지원을 받으세요.
