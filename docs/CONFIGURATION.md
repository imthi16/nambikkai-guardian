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
| `JWT_SECRET` | Future token-signing secret | Local-only value |

Known local secrets are rejected when `APP_ENV` is `staging` or `production`. Deployed secrets must
come from a secret manager or protected environment configuration, never a checked-in file. Keep
API docs disabled in deployments where public schema discovery is not intended.

Provider variables are placeholders until their dedicated features land. Empty `LLM_API_KEY`
means no cloud provider is enabled; do not insert fake credentials.
