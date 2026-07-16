# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

NambikkAI Guardian is a secure multilingual document-intelligence platform for Tamil, Tanglish, and
English. It answers only from evidence, attaches precise citations, verifies claims, detects
prompt-injection attempts, and abstains when evidence is insufficient. The full mission and
non-negotiable engineering rules live in [`AGENTS.md`](./AGENTS.md) — read it before making changes;
it is treated as authoritative for the whole repository.

**Current state**: this is an early-stage monorepo skeleton. Only health-check endpoints and CI/local
infra plumbing exist so far (see `git log`). Product features (auth, ingestion, retrieval, generation,
verification, safety) are tracked as separate issues in [`docs/BACKLOG.md`](./docs/BACKLOG.md) and
staged in [`docs/IMPLEMENTATION_PLAN.md`](./docs/IMPLEMENTATION_PLAN.md) — check those before assuming
a subsystem is implemented rather than planned.

## Commands

All commands run from the repo root via `make`; see `make help` for the full list.

```bash
cp .env.example .env      # one-time setup; local-only credentials, never reuse elsewhere
make install              # create apps/api/.venv and install apps/web via npm ci
make hooks                # install pre-commit hooks

make infra-up             # start PostgreSQL(+pgvector), Redis, MinIO; creates the local bucket
make infra-down            # stop infra, keep volumes
make infra-logs            # follow postgres/redis/minio logs

make dev-api               # uvicorn --reload on 127.0.0.1:8000 (health: /health, /api/v1/health)
make dev-web               # next dev on 127.0.0.1:3000

make format                # ruff format + ruff check --fix (api), prettier (web)
make format-check          # same, check-only
make lint                  # ruff check (api) + eslint --max-warnings=0 (web)
make typecheck             # strict mypy (api) + next typegen && tsc --noEmit (web)
make test                  # pytest (api, cov-fail-under=90) + vitest run --coverage (web, 90% thresholds)
make build                 # next build
make audit                 # pip-audit + npm audit --audit-level=high
make check                 # format-check + lint + typecheck + test + build + compose-config — run before considering a change done
make compose-build         # build non-root production api/web images
```

Single-test invocation (no Makefile shortcut — run directly against the venv/npm):

```bash
cd apps/api && .venv/bin/pytest tests/test_health.py -k some_case
npm --prefix apps/web run test -- path/to/file.test.tsx
```

Coverage gates are enforced and must not be lowered to make a change pass (`pyproject.toml`
`--cov-fail-under=90`; `vitest.config.ts` 90% branches/functions/lines/statements). Add real tests
instead. Note: running a single backend test file trips the `pytest-cov` fail-under gate because
coverage is measured against that subset, not the whole app — a failing coverage line there does not
mean the change is broken; run the full `.venv/bin/pytest` (or `make test`) to get the real number.

## Architecture

Target request flow (per `README.md` / `AGENTS.md`), most of which is not yet built:

```
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

Repository layout and intended ownership per directory:

- `apps/api` — FastAPI service. `app/main.py` builds the app via `create_app(settings)`; versioned
  routes are mounted under `/api/v1` through `app/api/v1/router.py`, which aggregates routers from
  `app/routes/`. Settings (`app/config.py`) load from process env or the root `.env`, located by
  walking up from the module path to find `AGENTS.md` — do not assume a fixed relative path to the
  env file. `Settings.enforce_deployment_secrets` rejects the checked-in default `JWT_SECRET` /
  `S3_SECRET_KEY` when `APP_ENV` is `staging` or `production`.
- `apps/web` — Next.js (App Router) + TypeScript, React 19. Strict TypeScript, strict ESLint
  (`--max-warnings=0`).
- `services/` — planned boundaries for `ingestion`, `retrieval`, `verification`, `safety`, kept as
  separate services rather than folded into `apps/api` so each enforces its own authorization and
  input-trust boundary.
- `packages/` — shared `contracts` (schemas / generated clients), `config`, `observability`
  (tracing/metrics/logging helpers) intended for reuse across `apps/*` and `services/*`.
- `infra/` — Alembic migrations + row-level security (`infra/migrations`), dashboards/alerts
  (`infra/monitoring`).
- `tests/` — cross-cutting `unit`, `integration`, `evaluation` (AI/retrieval regression) suites,
  distinct from the per-app test suites under `apps/api/tests` and `apps/web/**/*.test.tsx`.

Local infra (`docker-compose.yml`): `postgres` (pgvector/pgvector image), `redis`, `minio`, and a
one-shot `minio-create-bucket` job that must complete before the API container starts. `api`/`web`
containers are under the `application` compose profile (`make compose-build` builds them; `infra-up`
does not start them) and run `read_only: true` with a `tmpfs` `/tmp`.

## Non-obvious engineering rules

The full rules are in `AGENTS.md`; the ones most likely to be violated by an unfamiliar change:

- Treat uploaded files, OCR output, webpages, and retrieved chunks as **untrusted data** passed to
  the model, never as instructions — this applies to any ingestion or generation code, not just the
  safety service.
- Enforce workspace/document authorization inside the repository and retrieval layers, not only at
  the route level.
- Preserve full provenance on every chunk: document ID, version, page, section, offsets, language,
  OCR engine, confidence — downstream citation and verification depend on this being present from
  ingestion onward.
- A citation must support the exact claim (numbers, dates, conditions, negation); model-reported
  confidence alone is never a valid confidence score — verification must combine retrieval,
  reranking, OCR, and normalization signals.
- Keep LLM, embedding, OCR, and reranker providers behind interfaces (planned: PaddleOCR, BGE-M3,
  bge-reranker-v2-m3) so providers are swappable.
- MVP is read-only: no new external side effects without explicit approval and threat modelling.
- Any AI/RAG-affecting change needs a measurable regression/evaluation test, not just unit tests.
- Branches: `feat/`, `fix/`, `docs/`, `test/`, `chore/` prefixes; Conventional Commit messages; one
  issue/branch per reviewable feature; never merge automatically.
