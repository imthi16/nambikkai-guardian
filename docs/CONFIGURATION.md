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
| `S3_BUCKET` | Private document bucket | `nambikkai-documents` |
| `JWT_SECRET` | HS256 signing secret for access tokens | Local-only value |
| `ACCESS_TOKEN_TTL_SECONDS` | Access-token lifetime | `900` (15 minutes) |
| `REFRESH_TOKEN_TTL_SECONDS` | Refresh-token lifetime | `1209600` (14 days) |
| `AUTH_RATE_LIMIT_ATTEMPTS` | Allowed requests per auth endpoint per window | `10` |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | Rate-limit window length | `60` |
| `MAX_UPLOAD_BYTES` | Document upload size cap | `26214400` (25 MiB) |
| `DOWNLOAD_URL_TTL_SECONDS` | Presigned download-link lifetime | `300` |
| `INGESTION_QUEUE_KEY`, `INGESTION_DEAD_LETTER_KEY` | Redis list keys for the job queue | `nambikkai:ingestion:*` |
| `INGESTION_MAX_ATTEMPTS` | Attempts before a job dead-letters | `3` |
| `INGESTION_STALE_AFTER_SECONDS` | Age before running/queued jobs are recovered | `300` |

Known local secrets are rejected when `APP_ENV` is `staging` or `production`. Deployed secrets must
come from a secret manager or protected environment configuration, never a checked-in file. Keep
API docs disabled in deployments where public schema discovery is not intended.

Provider variables are placeholders until their dedicated features land. Empty `LLM_API_KEY`
means no cloud provider is enabled; do not insert fake credentials.
