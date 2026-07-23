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

## Multilingual embeddings

`app.embeddings` turns chunk and query text into dense vectors behind the
`EmbeddingProvider` protocol, so the local MVP provider can be replaced by a
hosted BGE-M3 deployment without changing persistence or retrieval. Every
provider declares `model`, `model_version`, and `dimensions`, and returns
typed vectors that are validated (count and width) before use.

The MVP ships `LocalHashingEmbeddingProvider`: a deterministic, dependency-free
provider that emits 1024-dim unit vectors (BGE-M3's width) from a hashed
bag-of-features over `app.language`-normalized text. It is a faithful wiring
stand-in (real dimensionality, deterministic per model version, multilingual)
but not a semantic model, so it is used for plumbing and tests, not quality
measurement. Batching and bounded-backoff retries are cross-cutting decorators
(`BatchingEmbeddingProvider`, `RetryingEmbeddingProvider`) that preserve the
provider contract and input order.

Vectors persist in `chunk_embeddings`, one row per chunk per model version, so
a model upgrade adds rows rather than overwriting reproducible provenance. The
table carries a denormalized `workspace_id`, row-level security matching the
other tenant tables, and an IVFFlat cosine index. `ChunkEmbeddingRepository`
is workspace-scoped: persistence checks the chunk belongs to the caller's
workspace, and cosine search filters by workspace and model version so
unauthorized vectors never leave the data layer. Telemetry records counts and
the model, never document text.

## Permission-filtered hybrid retrieval

`app.retrieval` answers a workspace query by running two retrievers and fusing
their rankings:

- **Lexical**: PostgreSQL full-text search over `chunks.content` using the
  `simple` text-search configuration, ranked with `ts_rank_cd` and backed by a
  GIN expression index (migration 0008). `simple` applies no language-specific
  stemming, so Tamil, English, and romanized Tanglish tokens are indexed and
  matched uniformly. Free-text queries are parsed with `websearch_to_tsquery`,
  which never raises on arbitrary punctuation, so untrusted query text cannot
  cause a parse error or injection.
- **Dense**: pgvector cosine search over `chunk_embeddings` for the query's
  own model version.

The query's `search_variants` (normalized, transliterated, expansions) drive
the lexical side so a Tanglish query can match Tamil-script content. Both
retrievers run through workspace-scoped repositories, so the workspace filter
(and row-level security beneath it) is applied *before* any candidate is
scored: there is no code path that returns a chunk another tenant owns.
Optional `document_id` and `language` filters narrow both sides identically.

Rankings are merged with **Reciprocal Rank Fusion** (`1 / (k + rank)`, default
`k = 60`), a pure, deterministic function that needs only ranks, not
comparable scores, which is exactly right for mixing `ts_rank_cd` relevance
with cosine similarity. Fused ids are hydrated into fully-provenanced results
(document, page, section, offsets, language, OCR) that downstream citation and
verification depend on. Every retrieval emits a structured `RetrievalTrace`
(candidate counts, per-source ranks, fused scores, filters, timings) that
carries no query text, chunk content, or secrets, so it is safe to log and
return. The endpoint `POST /workspaces/{id}/retrieval/search` requires the
`QUERY` capability and clamps caller-supplied `top_k` to a configured maximum.

## Multilingual reranking

`app.reranking` refines the fused candidate order with a cross-encoder-style
reranker behind the `Reranker` protocol, so the local MVP reranker can be
replaced by a hosted `bge-reranker-v2-m3` without touching retrieval. A
reranker scores how well each passage answers the query; `RerankService` then
min-max normalizes those raw scores into `[0, 1]` (so a threshold is
meaningful across models), drops candidates below `RERANK_THRESHOLD`, and
reorders by normalized score with ties broken by chunk id.

The MVP ships `LocalLexicalReranker`: a deterministic, dependency-free reranker
that scores query/passage relevance by blended coverage and Jaccard over
`app.language`-normalized unigrams and character trigrams, so Tamil, English,
and Tanglish are handled with no language-specific tables. It is a lexical
stand-in, not a semantic cross-encoder, so it is used for wiring and tests, not
quality measurement; a labelled multilingual evaluation asserts a minimum
top-1 accuracy and MRR to catch regressions.

Reranking is a stage inside `HybridRetrievalService`: when enabled, fusion
keeps a larger candidate pool (`RERANK_CANDIDATE_LIMIT`) so the reranker can
promote a chunk fusion ranked just outside `top_k`, then the reranked list is
truncated. The reranker only ever sees chunk text that was already authorized
and hydrated, so it can reorder or drop candidates but never widen the result
set or cross a tenant boundary. Failure is safe by construction: if the
reranker raises, the service preserves the fused order (flagged in telemetry)
rather than dropping authorized evidence. The retrieval trace records the
reranker model, latency, and dropped count, never passage text or the query.

## Grounded answer pipeline

`app.rag` turns an authorized query into a **grounded answer or a calibrated
abstention** with a typed [LangGraph](https://langchain-ai.github.io/langgraph/)
state machine. The graph's single state object is a validated Pydantic
`RagState`, so every transition is type-checked and the fields a node may read
or write are explicit. Nodes are small and each returns a partial update that
LangGraph merges back in.

The pipeline is a straight line with three hard gates that cannot be bypassed
because they are the graph's own conditional edges:

```text
authorize ─▶ analyze ─▶ retrieve ─▶ generate ─▶ verify ─▶ compose ─▶ answer
    │                       │                        │
    └── abstain ◀───────────┴── abstain ◀────────────┴── abstain
```

1. **authorize** — the request must carry a proven workspace scope (membership,
   role, and row-level security are already bound by the route dependency);
   otherwise the graph routes straight to abstention and retrieval never runs.
2. **retrieve** — evidence comes only through the workspace-scoped
   `HybridRetrievalService` behind the `EvidenceRetriever` port, so a node can
   only ever see this tenant's chunks. The **evidence-sufficiency gate** then
   abstains unless enough sufficiently-scored passages were found, so
   generation never runs on empty or thin evidence. Only the minimal top
   passages (`RAG_MAX_EVIDENCE`) reach generation.
3. **generate** — the `AnswerGenerator` proposes *candidate claims*, each a
   quote pinned to one supplied passage's `chunk_id`. The MVP `ExtractiveGenerator`
   selects the sentence best covering the query (unigram + character-trigram
   overlap, so English, Tamil, and Tanglish work with no language tables); a
   hosted LLM can replace it behind the same interface.
4. **verify** — the `ClaimVerifier` resolves each candidate's cited passage from
   the authorized set (rejecting any claim that cites an unknown chunk),
   confirms the quote actually occurs in that chunk, and assigns a *calibrated*
   confidence blending retrieval, rerank, OCR, and query-overlap signals rather
   than any model's self-reported score. Only `SUPPORTED` claims survive, so a
   hallucinated or paraphrased quote cannot be cited. The **support gate**
   abstains when nothing survives.
5. **compose** — the answer is assembled from supported claims only, with a
   citation per claim carrying exact provenance (document, version, page,
   section, in-chunk offsets, language). The outcome is `PARTIAL` when some
   candidates were dropped, `ANSWERED` otherwise.

Evidence content is untrusted data end to end: nodes quote, score, and cite it
but never treat anything it says as an instruction. Every run emits a
structured `RagTrace` (gate decisions, counts, outcome, timings, embedded
retrieval trace) that carries no query text, evidence text, answer text, or
secrets, so it is safe to log, return, and persist; `RagService` records it as
an append-only audit event. The endpoint `POST /workspaces/{id}/answer`
requires the `QUERY` capability and clamps caller-supplied `top_k` to
`RAG_MAX_TOP_K`. A deterministic, DB-free evaluation asserts the measurable
promises: answerable multilingual queries ground with exact citations, and
out-of-corpus queries abstain instead of inventing an answer.
