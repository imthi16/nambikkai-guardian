# Architecture

```text
Browser / Next.js
       |
FastAPI API Gateway
       |
Authentication + Workspace Authorization
       |
LangGraph Query Orchestrator
  | query normalization and Tanglish expansion
  | permission-filtered hybrid retrieval
  | multilingual reranking
  | grounded answer generation
  | atomic claim verification
  | confidence calibration and abstention
       |
PostgreSQL + pgvector | Redis | S3/MinIO
       |
Async ingestion workers
validate -> scan -> parse/OCR -> normalize -> chunk -> embed -> index
```

## Trust boundaries

- Uploaded documents, OCR output, webpages, and retrieved chunks are untrusted data.
- Workspace and document permissions must be applied before retrieval results leave the data layer.
- The generation model receives only the smallest evidence set needed for the answer.
- Each material answer claim must map to one or more source spans.
- Unsupported or contradictory claims are removed or cause the system to abstain.
- The MVP is read-only and cannot perform external side effects.

## Initial service boundaries

- `apps/web`: user interface, upload status, chat, citations, reviewer feedback.
- `apps/api`: public API, authentication, authorization, orchestration entry points.
- `services/ingestion`: validation, malware scanning, parsing, OCR, normalization, chunking.
- `services/retrieval`: lexical and dense search, fusion, filters, reranking.
- `services/verification`: claim splitting, evidence verification, contradiction checks, abstention.
- `services/safety`: prompt-injection detection, sanitization, quarantine decisions.

## Language detection and normalization

The query pipeline (`app.language`) turns raw user text into a
`ProcessedQuery` before retrieval, keeping three representations so intent is
never lost:

- `original`: the user's exact input, retained verbatim for provenance.
- `normalized`: Unicode NFC, folded smart/full-width punctuation, and
  collapsed whitespace. Idempotent and safe to index.
- `transliterated`: Tanglish (romanized Tamil) rendered into Tamil script so
  Latin-typed queries can match Tamil-script documents. For Tamil and English
  it repeats `normalized`.

Detection is deterministic and explainable: it measures Tamil-vs-Latin letter
ratios, then disambiguates Latin-only text into English or Tanglish using a
small, auditable marker lexicon. Every result carries a calibrated
`confidence` and a `limitations` list (for example "mixed Tamil and Latin
script" or "ambiguous romanized text"), which downstream retrieval uses to
widen candidates when the signal is weak.

Detection output is untrusted metadata: it informs retrieval and is never fed
to the model as an instruction. Transliteration and spelling normalization sit
behind the `Transliterator` and `SpellingNormalizer` protocols so rule-based
MVP providers can be replaced without touching the orchestration.
