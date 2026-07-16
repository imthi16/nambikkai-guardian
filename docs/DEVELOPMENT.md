# Development Guide

## Prerequisites

- Python 3.12 or newer
- Node.js 22 and npm
- Docker with Compose v2+
- GNU Make and Git

Copy `.env.example` to `.env`; its credentials are local-only. Never reuse them outside a local or
test environment. Install application dependencies with `make install`, then install hooks with
`make hooks`.

## Local services

Run `make infra-up` to start health-checked PostgreSQL with pgvector, Redis, MinIO, and the local
`nambikkai-documents` bucket. Ports bind to `127.0.0.1`. `make infra-logs` follows service logs and
`make infra-down` stops services without deleting volumes.

Start the API with `make dev-api` and visit `http://127.0.0.1:8000/health` or the versioned
`/api/v1/health`. In a second terminal, start Next.js with `make dev-web` and visit
`http://127.0.0.1:3000`.

## Database migrations

Apply schema migrations with `make migrate-up` and revert the latest one with
`make migrate-down`. After changing models under `apps/api/app/db/models`, generate a
new revision with `make migrate-new m="describe change"` and review it before committing.
The API integration tests provision disposable `nambikkai_test` and
`nambikkai_migration_test` databases on the local PostgreSQL instance, so `make test`
requires `make infra-up` to be running.

## Authentication

The API exposes `/api/v1/auth` endpoints: `register`, `login`, `refresh`, `logout`, and `me`.
Passwords are hashed with Argon2id. Logins return a short-lived HS256 access token (sign-key
`JWT_SECRET`) plus an opaque refresh token whose SHA-256 digest is stored in `refresh_tokens`.
Refreshing rotates the token and revokes the presented one; reusing a revoked token revokes every
session for that account, and logout revokes a single session. Auth failures use stable error
codes (`invalid_credentials`, `invalid_refresh_token`, `not_authenticated`,
`email_already_registered`, `rate_limited`) so clients never parse messages. The credential
endpoints are rate limited per client IP and path with an in-process sliding window; the limiter
sits behind an interface and must move to Redis before the API scales past one replica.

## Verification

- `make format` formats Python and web sources.
- `make lint` runs Ruff and ESLint.
- `make typecheck` runs strict mypy and TypeScript checks.
- `make test` runs backend and frontend coverage suites.
- `make build` creates the production Next.js bundle.
- `make audit` checks installed Python and locked npm dependencies for known vulnerabilities.
- `make compose-build` builds non-root API and web images; the API build also imports the packaged
  application to catch runtime dependency or container-layout configuration failures.
- `make check` runs the primary local quality suite.

Do not lower a threshold to make a change pass. Add deterministic tests for real behavior and add
evaluation cases for retrieval, model, prompt, or verification changes.

## Branch and review workflow

Create one issue and one branch per reviewable feature, for example `feat/project-foundation`.
Use Conventional Commits such as `feat: add versioned health routes`. Review `git diff` before
staging, target `main`, complete every PR template section, and never merge automatically.
