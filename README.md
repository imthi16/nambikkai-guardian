# NambikkAI Guardian

A secure multilingual document-intelligence platform for **Tamil, Tanglish, and English**. It is designed to answer from evidence, attach precise citations, verify claims, detect prompt-injection attempts, and abstain when the available evidence is insufficient.

## Product goal

Most document-chat applications optimize for fluent answers. NambikkAI Guardian optimizes for **trust**:

- every answer is grounded in authorized documents;
- every material claim links to supporting evidence;
- unsupported or contradictory claims are removed;
- suspicious document instructions are treated as untrusted data;
- low-confidence questions receive a refusal or clarification request.

## MVP scope

- User authentication, workspaces, and role-based access
- Secure PDF and image upload
- Parsing, OCR, language detection, normalization, and chunking
- Tamil, Tanglish, and English query processing
- Hybrid lexical and vector retrieval with reranking
- Evidence-grounded answers with page and span citations
- Claim-level verification and calibrated abstention
- Prompt-injection detection and quarantine
- Audit logs, evaluation datasets, monitoring, and deployment

## Starter architecture

```text
Browser / Next.js
       |
FastAPI API
       |
Authorization + workspace boundary
       |
LangGraph query workflow
  normalize -> retrieve -> rerank -> generate -> verify -> abstain/cite
       |
PostgreSQL + pgvector | Redis | S3/MinIO
       |
Async ingestion workers
  validate -> scan -> parse/OCR -> normalize -> chunk -> embed -> index
```

## Repository structure

```text
apps/
  api/                   FastAPI service
  web/                   Next.js application
services/                Ingestion, retrieval, verification, and safety boundaries
packages/                Shared contracts, configuration, and observability
infra/                   Migrations, containers, and monitoring
tests/                   Cross-service integration and evaluation suites
docs/                    Architecture, backlog, configuration, and development guides
```

## Local setup

### 1. Install dependencies

```bash
cp .env.example .env
make install
```

### 2. Start infrastructure

```bash
make infra-up
```

### 3. Start the applications

Run these in separate terminals:

```bash
make dev-api
make dev-web
```

Open `http://127.0.0.1:8000/api/v1/health` and `http://127.0.0.1:3000`.

Run the complete local quality suite with `make check`. See
[`docs/DEVELOPMENT.md`](./docs/DEVELOPMENT.md) for all commands and
[`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) for environment settings.

## Development guidance

Read [`AGENTS.md`](./AGENTS.md) before using Codex, Claude Code, or another coding agent. The
complete staged build is in [`docs/IMPLEMENTATION_PLAN.md`](./docs/IMPLEMENTATION_PLAN.md), and
the issue dependency graph is in [`docs/BACKLOG.md`](./docs/BACKLOG.md).

## Current status

The engineering foundation is under active review in issue #1. Product features remain deliberately
separated into issues #2–#26 so database, authentication, tenancy, ingestion, retrieval, generation,
verification, security, UI, evaluation, and deployment each receive a focused pull request.
