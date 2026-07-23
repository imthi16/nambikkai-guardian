# Configuration Reference

Configuration is read from environment variables and, for local development, the root `.env`.
The committed `.env.example` contains non-secret local defaults only.

| Variable | Purpose | Local default |
| --- | --- | --- |
| `APP_ENV` | `development`, `test`, `staging`, or `production` | `development` |
| `APP_VERSION` | API-reported application version | `0.1.0` |
| `API_HOST`, `API_PORT` | API bind address and port | `0.0.0.0`, `8000` |
| `API_DOCS_ENABLED` | Enables OpenAPI, Swagger UI, and ReDoc | `true` |
| `DATABASE_URL` | Async PostgreSQL connection URL | Local PostgreSQL |
| `REDIS_URL` | Queue/cache connection URL | Local Redis |
| `S3_ENDPOINT` | S3-compatible endpoint | Local MinIO |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Object-storage credentials | Local-only values |
| `S3_BUCKET` | Private document bucket | `attest-documents` |
| `JWT_SECRET` | HS256 signing secret for access tokens | Local-only value |
| `ACCESS_TOKEN_TTL_SECONDS` | Access-token lifetime | `900` (15 minutes) |
| `REFRESH_TOKEN_TTL_SECONDS` | Refresh-token lifetime | `1209600` (14 days) |
| `AUTH_RATE_LIMIT_ATTEMPTS` | Allowed requests per auth endpoint per window | `10` |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | Rate-limit window length | `60` |
| `MAX_UPLOAD_BYTES` | Document upload size cap | `26214400` (25 MiB) |
| `DOWNLOAD_URL_TTL_SECONDS` | Presigned download-link lifetime | `300` |
| `INGESTION_QUEUE_KEY`, `INGESTION_DEAD_LETTER_KEY` | Redis list keys for the job queue | `attest:ingestion:*` |
| `INGESTION_MAX_ATTEMPTS` | Attempts before a job dead-letters | `3` |
| `INGESTION_STALE_AFTER_SECONDS` | Age before running/queued jobs are recovered | `300` |
| `INGESTION_STORE_PAGE_IMAGES` | Store rendered PNGs of OCR'd pages | `true` |
| `OCR_ENGINE` | `none`, `tesseract`, or `paddle` | `none` |
| `OCR_LANGUAGES` | OCR language codes (`tam+eng`); `paddle` uses the first recognised code | `tam+eng` |
| `CHUNK_MAX_CHARS` | Maximum characters per chunk | `1200` |
| `CHUNK_OVERLAP_CHARS` | Context shared between neighboring chunks | `150` |
| `EMBEDDING_PROVIDER` | Embedding backend (`local`) | `local` |
| `EMBEDDING_MODEL`, `EMBEDDING_MODEL_VERSION` | Provider provenance stored on every vector | `bge-m3-local`, `hashing-v1` |
| `EMBEDDING_DIMENSIONS` | Vector width; must match the `chunk_embeddings` column | `1024` |
| `EMBEDDING_BATCH_SIZE` | Inputs per provider call | `32` |
| `EMBEDDING_MAX_ATTEMPTS`, `EMBEDDING_BACKOFF_SECONDS` | Retry budget for transient provider errors | `3`, `0.5` |
| `RETRIEVAL_RRF_K` | Reciprocal Rank Fusion constant (larger flattens rank advantage) | `60` |
| `RETRIEVAL_CANDIDATE_LIMIT` | Max candidates fetched per retriever before fusion | `50` |
| `RETRIEVAL_TOP_K` | Default number of fused results returned | `10` |
| `RETRIEVAL_MAX_TOP_K` | Upper bound a request's `top_k` is clamped to | `50` |
| `RERANK_ENABLED` | Rerank fused candidates before returning | `true` |
| `RERANK_THRESHOLD` | Minimum normalized rerank score (0-1) to keep a candidate | `0.0` |
| `RERANK_CANDIDATE_LIMIT` | Fused candidates fed to the reranker before truncation | `30` |
| `RAG_TOP_K` | Default evidence passages retrieved for a grounded answer | `8` |
| `RAG_MAX_TOP_K` | Upper bound a request's answer `top_k` is clamped to | `20` |
| `RAG_MAX_EVIDENCE` | Max passages sent to generation (kept minimal) | `6` |
| `RAG_MIN_EVIDENCE` | Passages required to clear the sufficiency gate, else abstain | `1` |
| `RAG_MIN_EVIDENCE_SCORE` | Minimum fused score a passage needs to count as evidence | `0.0` |

Known local secrets are rejected when `APP_ENV` is `staging` or `production`. Deployed secrets must
come from a secret manager or protected environment configuration, never a checked-in file. Keep
API docs disabled in deployments where public schema discovery is not intended.

## OCR engines

`OCR_ENGINE=tesseract` requires the system `tesseract` binary with the `tam` and `eng` language
models. `OCR_ENGINE=paddle` requires the optional Paddle dependencies, which are not in the base
image because they are large native wheels. To run the PaddleOCR engine, install the extra:

- Local: `pip install -e 'apps/api[paddle]'`
- Container: build with the `PIP_EXTRAS` build arg, for example
  `docker build --build-arg PIP_EXTRAS='[paddle]' apps/api`.

Selecting an engine whose dependency is absent fails fast at recognition time, so match `OCR_ENGINE`
to the image the worker actually runs.

Provider variables are placeholders until their dedicated features land. Empty `LLM_API_KEY`
means no cloud provider is enabled; do not insert fake credentials.
