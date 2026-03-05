# KGN — 시작 가이드

누구나 따라할 수 있는 쉬운 설치 및 사용 가이드입니다.

---

## KGN이 뭔가요?

KGN은 AI 에이전트(Claude 등)에게 **장기 기억**을 부여하는 도구입니다.

일반적으로 AI는 대화가 끝나면 모든 것을 잊어버립니다. KGN은 지식을 데이터베이스에 저장해서, AI가 과거 작업을 기억하고 활용할 수 있게 해줍니다.

**한줄 요약:** 메모를 작성 → KGN이 저장 → AI가 언제든 읽고 검색 가능

---

## 시작하기 전에

세 가지를 설치해야 합니다. 하나씩 차근차근 안내해 드릴게요.

| 무엇 | 왜 필요한가 | 소요 시간 |
|---|---|---|
| Python 3.12+ | KGN이 Python으로 만들어져 있습니다 | ~5분 |
| Docker Desktop | 데이터베이스를 자동으로 실행해 줍니다 | ~10분 |
| KGN | 실제 사용할 도구입니다 | ~2분 |

---

## 1단계: Python 설치

### Windows

1. [python.org/downloads](https://www.python.org/downloads/)에 접속합니다
2. 노란색 **"Download Python 3.12.x"** 버튼을 클릭합니다
3. 다운로드된 설치 파일을 실행합니다
4. **⚠️ 중요:** 하단의 **"Add python.exe to PATH"** 체크박스를 꼭 체크하세요
5. **"Install Now"** 클릭

### Mac

```bash
# Homebrew가 있다면:
brew install python@3.12

# 없다면 python.org/downloads에서 다운로드
```

### 잘 설치되었는지 확인

터미널을 열고 (Windows는 명령 프롬프트, Mac은 터미널) 다음을 입력하세요:

```bash
python --version
```

`Python 3.12.x` 같은 문구가 나오면 성공입니다. 에러가 나오면 컴퓨터를 재시작한 뒤 다시 시도해 보세요.

---

## 2단계: Docker Desktop 설치

Docker는 데이터베이스(PostgreSQL)를 자동으로 실행해 줍니다. 데이터베이스에 대해 몰라도 괜찮아요.

1. [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/)에 접속합니다
2. 사용 중인 OS(Windows / Mac / Linux)에 맞는 버전을 다운로드합니다
3. 설치하고 Docker Desktop을 실행합니다
4. 작업표시줄의 고래 아이콘이 **"Docker Desktop is running"** 상태가 될 때까지 대기합니다

> **💡 팁:** KGN을 사용할 때마다 Docker Desktop이 실행 중이어야 합니다. 로그인 시 자동 실행으로 설정해 두면 편합니다.

---

## 3단계: KGN 설치

터미널을 열고 다음 명령어를 입력하세요:

```bash
pip install kgn-mcp
```

완료! 설치 확인:

```bash
kgn --help
```

사용 가능한 명령어 목록이 보이면 성공입니다.

---

## 4단계: 데이터베이스 시작

KGN에는 바로 사용할 수 있는 데이터베이스 설정이 포함되어 있습니다:

```bash
# 먼저 KGN 소스 코드를 받아옵니다 (Docker 설정 파일이 필요)
git clone https://github.com/baobab00/kgn.git
cd kgn

# 데이터베이스 시작
docker compose -f docker/docker-compose.yml up -d postgres
```

> **지금 무슨 일이 일어났나요?** Docker가 PostgreSQL 데이터베이스를 백그라운드에서 다운로드하고 시작했습니다. 별도의 창이 뜨지 않고 조용히 실행됩니다.

### 데이터베이스가 잘 실행 중인지 확인

```bash
docker ps
```

"postgres"가 포함된 컨테이너가 "Up" 상태로 보이면 성공입니다.

---

## 5단계: 첫 프로젝트 만들기

```bash
kgn init --project my-first-project
```

성공 메시지가 나타나면 프로젝트가 준비된 것입니다!

---

## 6단계: 첫 번째 지식 노드 추가

KGN은 `.kgn` 확장자의 간단한 텍스트 파일을 사용합니다. 하나 만들어 볼까요?

### `my-note.kgn` 파일 만들기

아무 텍스트 편집기(메모장, VS Code 등)를 열고 다음 내용을 붙여넣으세요:

```yaml
---
kgn_version: "0.1"
id: "new:my-first-note"
type: SPEC
title: "나의 첫 번째 지식 노드"
status: ACTIVE
project_id: "my-first-project"
agent_id: "human"
tags: ["getting-started", "hello"]
confidence: 0.9
---

## Context

KGN을 처음 사용해 봅니다. 지식 노드가 어떻게 작동하는지 배우고 있어요.

## Content

지식 노드는 AI가 이해하고 검색할 수 있는 스마트 메모와 같습니다.
각 노드에는 다음이 포함됩니다:
- **type** (유형): GOAL, SPEC, TASK 등
- **status** (상태): ACTIVE, DEPRECATED 등
- **tags** (태그): 쉬운 검색을 위한 키워드
- **body** (본문): 실제 내용
```

### 파일을 저장한 후, KGN에 등록합니다

```bash
kgn ingest my-note.kgn --project my-first-project
```

🎉 **축하합니다!** AI가 접근할 수 있는 첫 번째 지식을 추가했습니다.

---

## 7단계: 프로젝트 확인

```bash
# 프로젝트 개요 확인
kgn status --project my-first-project

# 그래프 건강 상태 확인
kgn health --project my-first-project

# 내 노드 검색
kgn query nodes --project my-first-project --type SPEC
```

---

## 8단계: Claude에 연결하기 (선택사항)

이 부분이 KGN의 진가가 드러나는 곳입니다 — Claude가 여러분의 지식 그래프에 접근할 수 있게 됩니다.

### Claude Desktop 연결

1. Claude Desktop 설정 파일을 찾으세요:
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
   - **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`

2. 파일에 다음 내용을 추가하세요:

```json
{
  "mcpServers": {
    "kgn": {
      "command": "kgn",
      "args": ["mcp", "serve", "--project", "my-first-project"]
    }
  }
}
```

3. Claude Desktop을 재시작하세요

4. 🔨 (망치) 아이콘이 보이면 KGN 도구가 사용 가능한 상태입니다!

이제 Claude가 여러분의 지식 그래프를 읽고, 검색하고, 작성할 수 있습니다.

### Claude Code (터미널) 연결

```bash
claude mcp add kgn -- kgn mcp serve --project my-first-project
```

---

## 다음 단계

KGN이 실행되고 있으니, 이런 것들을 시도해 보세요:

### 📁 예제 파일 등록하기

KGN에 포함된 예제 파일을 바로 사용해 볼 수 있습니다:

```bash
kgn ingest examples/ --project my-first-project --recursive
```

### 🔍 AI 유사도 검색

OpenAI API 키가 있다면 의미 기반 검색을 활성화할 수 있습니다:

```bash
# API 키 설정 (.env 파일 생성)
echo "KGN_OPENAI_API_KEY=sk-your-key-here" > .env

# 임베딩과 함께 다시 등록
kgn ingest examples/ --project my-first-project --recursive --embed

# 유사한 노드 찾기
kgn query similar <node-id> --project my-first-project --top 5
```

### 🌐 웹 대시보드

지식 그래프를 시각적으로 확인하세요:

```bash
pip install kgn-mcp[web]
kgn web serve --project my-first-project --port 8080
```

브라우저에서 http://localhost:8080 을 열어보세요.

### 📝 VS Code 확장

`.kgn` 파일의 구문 강조 및 유효성 검사를 사용하세요:

```bash
code --install-extension baobab00.vscode-kgn
```

---

## 자주 발생하는 문제

<details>
<summary><b>"kgn" 명령어를 찾을 수 없다는 에러</b></summary>

Python의 Scripts 폴더가 PATH에 포함되지 않은 경우입니다.

**해결 방법 (Windows):**
1. `Win + R`을 누르고 `sysdm.cpl`을 입력 후 Enter
2. **고급** → **환경 변수** 클릭
3. **Path**에 추가: `C:\Users\<사용자명>\AppData\Local\Programs\Python\Python312\Scripts`
4. 터미널을 재시작하세요

**해결 방법 (Mac/Linux):**
```bash
export PATH="$HOME/.local/bin:$PATH"
```
이 줄을 `~/.bashrc` 또는 `~/.zshrc` 파일에 추가하세요.

</details>

<details>
<summary><b>Docker: "Cannot connect to the Docker daemon" 에러</b></summary>

Docker Desktop이 실행되지 않은 상태입니다. Docker Desktop을 열고 완전히 시작될 때까지 기다리세요 (작업표시줄의 고래 아이콘 확인).

</details>

<details>
<summary><b>데이터베이스 연결 에러</b></summary>

PostgreSQL 컨테이너가 실행 중인지 확인하세요:

```bash
docker ps
```

목록에 없다면 다시 시작하세요:

```bash
docker compose -f docker/docker-compose.yml up -d postgres
```

</details>

<details>
<summary><b>"Permission denied" 에러</b></summary>

**Windows:** 터미널을 관리자 권한으로 실행하세요.

**Mac/Linux:** 명령어 앞에 `sudo`를 추가하거나, `pip install --user kgn-mcp`를 사용하세요.

</details>

---

## 명령어 요약

| 하고 싶은 것 | 명령어 |
|---|---|
| 프로젝트 만들기 | `kgn init --project <이름>` |
| 지식 추가 | `kgn ingest <파일.kgn> --project <이름>` |
| 폴더 전체 추가 | `kgn ingest <폴더>/ --project <이름> --recursive` |
| 상태 확인 | `kgn status --project <이름>` |
| 노드 검색 | `kgn query nodes --project <이름>` |
| MCP 서버 시작 | `kgn mcp serve --project <이름>` |
| 웹 대시보드 | `kgn web serve --project <이름>` |
| 전체 명령어 보기 | `kgn --help` |

---

**도움이 더 필요하신가요?** [전체 README](README.md)를 확인하거나 [GitHub Issue](https://github.com/baobab00/kgn/issues)를 등록해 주세요.
