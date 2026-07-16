# Implementation Plan

## Phase 0 — Product baseline

- Finalize user personas, supported document types, refusal policy, and measurable MVP targets.
- Create a threat model, data-classification policy, and architecture decision records.

## Phase 1 — Foundation

- Configure the monorepo, local infrastructure, CI, linting, typing, tests, and secret scanning.
- Implement FastAPI and Next.js foundations, environment validation, authentication, workspaces, and RBAC.
- Create the PostgreSQL schema, Alembic migrations, tenant isolation, and audit-event foundation.

## Phase 2 — Secure ingestion

- Add signed uploads, MIME/extension/size/hash validation, malware scanning, and object storage.
- Implement PDF extraction, page rendering, OCR, layout metadata, and language identification.
- Chunk text while preserving page, section, offsets, OCR confidence, and document-version provenance.
- Add asynchronous jobs, retries, dead-letter handling, and an ingestion-status interface.

## Phase 3 — Multilingual retrieval

- Implement Tamil Unicode normalization and Tanglish transliteration/query expansion.
- Combine lexical retrieval with BGE-M3 dense retrieval.
- Add reciprocal-rank fusion, authorization filters, metadata filters, and multilingual reranking.
- Build a retrieval evaluation set and track Recall@K, MRR, and nDCG.

## Phase 4 — Grounded answers

- Build a LangGraph workflow: authorize → normalize → retrieve → rerank → generate → cite.
- Return structured answers containing atomic claims and exact evidence spans.
- Build a citation viewer with document page preview and highlighted evidence.
- Add streaming responses, timeouts, retries, and stable error handling.

## Phase 5 — Verification and abstention

- Add atomic claim splitting, evidence verification, and contradiction detection.
- Calibrate confidence using retrieval, reranking, OCR, normalization, and verifier signals.
- Remove unsupported claims or refuse the complete answer.
- Capture reviewer feedback, corrections, and evaluation outcomes.

## Phase 6 — Safety and production hardening

- Add direct and indirect prompt-injection detection, heuristics, quarantine, and safe prompt boundaries.
- Add rate limiting, signed URLs, CSP, secure headers, encryption, and retention/deletion workflows.
- Add OpenTelemetry traces, metrics, logs, alerting, cost tracking, backups, and restore procedures.
- Complete load tests, security tests, red-team evaluation, deployment, and operational runbooks.

## Suggested 12-week build

1. Repository, CI, Docker, and API/web skeleton.
2. Authentication, workspaces, RBAC, schema, and audit logs.
3. Secure upload and ingestion jobs.
4. Parsing, OCR, language metadata, and chunking.
5. Dense and lexical retrieval.
6. Tanglish normalization, fusion, and reranking.
7. Answer workflow and structured citations.
8. Citation UI and document viewer.
9. Claim verifier and contradiction checks.
10. Confidence calibration and abstention.
11. Prompt-injection defence and security regression tests.
12. Evaluation, observability, deployment, demo, and portfolio documentation.
