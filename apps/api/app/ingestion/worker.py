"""The ingestion worker: deterministic stage transitions with safe retries.

Design notes:

- The database is the source of truth; the queue only carries pointers, so
  duplicate delivery is always safe — claiming is a compare-and-set on the
  job row and terminal states are never reprocessed.
- Each stage transition commits its own transaction, making progress
  observable through the status API while a job runs.
- Failures split into quarantine (malware verdicts — terminal, never
  retried), permanent (integrity/content violations — terminal, dead-letter),
  and transient (everything else — retried up to `max_attempts`, then
  dead-letter).
- `requeue_stale` recovers jobs whose worker died mid-run and queued jobs
  whose enqueue was lost. It scans across workspaces, so a deployed worker
  needs a database role with BYPASSRLS; per-job work binds the workspace
  from the queue message instead.

Run standalone with `python -m app.ingestion.worker` (see `make dev-worker`).
"""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chunking.chunker import PageInput, chunk_pages
from app.chunking.provenance import ProvenanceError, validate_chunk_provenance
from app.db.models.documents import Chunk, Document, DocumentVersion, Page
from app.db.models.enums import DocumentStatus, IngestionStage, IngestionStatus
from app.db.models.operations import IngestionJob
from app.db.repositories.audit import AuditLogRepository
from app.db.session import bind_workspace, session_scope
from app.documents.validation import UploadRejectedError, detect_kind, verify_content
from app.ingestion.queue import JobMessage, JobQueue
from app.ingestion.scanner import MalwareScanner
from app.parsing.ocr import NullOcrEngine, OcrEngine
from app.parsing.pdf import parse_pdf, render_pdf_page_png
from app.parsing.text import parse_docx, parse_text
from app.parsing.types import ParsedDocument, ParserError
from app.storage.base import ObjectStorage

logger = logging.getLogger("app.ingestion")

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Stages after chunking are placeholders until their features land
# (#9 normalization, #10 embeddings/indexing).
_PLACEHOLDER_STAGES = (
    IngestionStage.NORMALIZING,
    IngestionStage.EMBEDDING,
    IngestionStage.INDEXING,
)


class QuarantinedError(Exception):
    """The scanner flagged the content; the document must be quarantined."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PermanentIngestionError(Exception):
    """The job can never succeed (integrity or content violation)."""


@dataclass(frozen=True)
class _LoadedContent:
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    mime_type: str
    data: bytes


class IngestionWorker:
    """Processes queued ingestion jobs one at a time."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        queue: JobQueue,
        scanner: MalwareScanner,
        ocr_engine: OcrEngine | None = None,
        store_page_images: bool = True,
        chunk_max_chars: int = 1200,
        chunk_overlap_chars: int = 150,
        max_attempts: int = 3,
        stale_after_seconds: int = 300,
    ) -> None:
        self._factory = session_factory
        self._storage = storage
        self._queue = queue
        self._scanner = scanner
        self._ocr_engine = ocr_engine or NullOcrEngine()
        self._store_page_images = store_page_images
        self._chunk_max_chars = chunk_max_chars
        self._chunk_overlap_chars = chunk_overlap_chars
        self._max_attempts = max_attempts
        self._stale_after = timedelta(seconds=stale_after_seconds)

    async def process_next(self, timeout_seconds: float = 1.0) -> bool:
        """Take one message off the queue; returns False when idle."""
        message = await self._queue.dequeue(timeout_seconds)
        if message is None:
            return False
        await self.process(message)
        return True

    async def process(self, message: JobMessage) -> None:
        if not await self._claim(message):
            return
        try:
            await self._run_stages(message)
        except QuarantinedError as verdict:
            await self._quarantine(message, verdict.reason)
        except PermanentIngestionError as error:
            await self._fail(message, str(error), retry=False)
        except Exception as error:  # noqa: BLE001 - the worker must survive any job error
            await self._fail(message, f"{type(error).__name__}: {error}", retry=True)
        else:
            await self._finish(message)

    async def _claim(self, message: JobMessage) -> bool:
        """Compare-and-set QUEUED -> RUNNING; anything else is a duplicate."""
        async with session_scope(self._factory) as session:
            await bind_workspace(session, message.workspace_id)
            job = await session.get(IngestionJob, message.job_id, with_for_update=True)
            if job is None:
                logger.warning("ingestion job missing", extra={"job_id": str(message.job_id)})
                return False
            if job.status is not IngestionStatus.QUEUED:
                logger.info(
                    "duplicate or in-flight delivery ignored",
                    extra={"job_id": str(job.id), "status": job.status.value},
                )
                return False
            job.status = IngestionStatus.RUNNING
            job.started_at = datetime.now(UTC)
            job.attempts += 1
            job.error = None
            document = await session.get(Document, job.document_id)
            if document is not None:
                document.status = DocumentStatus.PROCESSING
            logger.info(
                "ingestion started",
                extra={"job_id": str(job.id), "attempt": job.attempts},
            )
            return True

    async def _run_stages(self, message: JobMessage) -> None:
        content = await self._stage_validate(message)
        await self._stage_scan(message, content)
        parsed = await self._stage_parse(message, content)
        await self._stage_ocr(message, content, parsed)
        await self._stage_chunk(message, content, parsed)
        for stage in _PLACEHOLDER_STAGES:
            await self._advance_stage(message, stage)
            logger.info(
                "stage is a placeholder until its feature lands",
                extra={"job_id": str(message.job_id), "stage": stage.value},
            )

    async def _advance_stage(self, message: JobMessage, stage: IngestionStage) -> None:
        async with session_scope(self._factory) as session:
            await bind_workspace(session, message.workspace_id)
            job = await session.get(IngestionJob, message.job_id)
            assert job is not None  # noqa: S101 - claimed above, row cannot vanish
            job.stage = stage
        logger.info(
            "stage reached",
            extra={"job_id": str(message.job_id), "stage": stage.value},
        )

    async def _stage_validate(self, message: JobMessage) -> _LoadedContent:
        await self._advance_stage(message, IngestionStage.VALIDATING)
        async with session_scope(self._factory) as session:
            await bind_workspace(session, message.workspace_id)
            job = await session.get(IngestionJob, message.job_id)
            assert job is not None  # noqa: S101 - claimed above
            document = await session.get(Document, job.document_id)
            if document is None:
                msg = "document row is gone"
                raise PermanentIngestionError(msg)
            version = await session.scalar(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.version_number.desc())
                .limit(1)
            )
            if version is None:
                msg = "document has no stored version"
                raise PermanentIngestionError(msg)
            storage_key = version.storage_key
            expected_sha256 = version.sha256
            filename = document.source_filename
            loaded = _LoadedContent(
                document_id=document.id,
                version_id=version.id,
                version_number=version.version_number,
                mime_type=document.mime_type,
                data=b"",
            )

        data = await self._storage.get_object(storage_key)
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            msg = "stored object does not match its recorded hash"
            raise PermanentIngestionError(msg)
        try:
            verify_content(detect_kind(filename), data)
        except UploadRejectedError as rejection:
            raise PermanentIngestionError(rejection.message) from rejection
        return _LoadedContent(
            document_id=loaded.document_id,
            version_id=loaded.version_id,
            version_number=loaded.version_number,
            mime_type=loaded.mime_type,
            data=data,
        )

    async def _stage_scan(self, message: JobMessage, content: _LoadedContent) -> None:
        await self._advance_stage(message, IngestionStage.SCANNING)
        verdict = await self._scanner.scan(content.data)
        if not verdict.clean:
            raise QuarantinedError(verdict.reason or "malware detected")

    async def _stage_parse(self, message: JobMessage, content: _LoadedContent) -> ParsedDocument:
        await self._advance_stage(message, IngestionStage.PARSING)
        try:
            if content.mime_type == "application/pdf":
                parsed = parse_pdf(content.data)
            elif content.mime_type == _DOCX_MIME:
                parsed = parse_docx(content.data)
            else:
                parsed = parse_text(content.data)
        except ParserError as error:
            raise PermanentIngestionError(str(error)) from error
        logger.info(
            "parsed document",
            extra={
                "job_id": str(message.job_id),
                "parser": parsed.parser,
                "pages": len(parsed.pages),
                "scanned_pages": sum(1 for page in parsed.pages if page.needs_ocr),
            },
        )
        return parsed

    async def _stage_ocr(
        self,
        message: JobMessage,
        content: _LoadedContent,
        parsed: ParsedDocument,
    ) -> None:
        await self._advance_stage(message, IngestionStage.OCR)
        for page in parsed.pages:
            if not page.needs_ocr:
                continue
            image_png = render_pdf_page_png(content.data, page.page_number)
            if self._store_page_images:
                image_key = (
                    f"workspaces/{message.workspace_id}/documents/{content.document_id}"
                    f"/pages/v{content.version_number}/p{page.page_number}.png"
                )
                await self._storage.put_object(image_key, image_png, "image/png")
                page.image_storage_key = image_key
            result = await self._ocr_engine.recognize(image_png)
            page.text = result.text
            page.ocr_engine = self._ocr_engine.name
            page.ocr_confidence = result.confidence
            page.ocr_blocks = result.blocks or None
            logger.info(
                "page ocr complete",
                extra={
                    "job_id": str(message.job_id),
                    "page": page.page_number,
                    "engine": self._ocr_engine.name,
                    "confidence": result.confidence,
                },
            )
        await self._persist_pages(message, content, parsed)

    async def _persist_pages(
        self,
        message: JobMessage,
        content: _LoadedContent,
        parsed: ParsedDocument,
    ) -> None:
        """Replace the version's pages atomically; reprocessing never duplicates."""
        async with session_scope(self._factory) as session:
            await bind_workspace(session, message.workspace_id)
            await session.execute(
                delete(Page).where(Page.document_version_id == content.version_id)
            )
            for page in parsed.pages:
                session.add(
                    Page(
                        document_version_id=content.version_id,
                        page_number=page.page_number,
                        text=page.text,
                        ocr_engine=page.ocr_engine,
                        ocr_confidence=page.ocr_confidence,
                        image_storage_key=page.image_storage_key,
                        ocr_blocks=(
                            [block.as_provenance() for block in page.ocr_blocks]
                            if page.ocr_blocks
                            else None
                        ),
                    )
                )
            version = await session.get(DocumentVersion, content.version_id)
            if version is not None:
                version.page_count = len(parsed.pages)

    async def _stage_chunk(
        self,
        message: JobMessage,
        content: _LoadedContent,
        parsed: ParsedDocument,
    ) -> None:
        """Chunk parsed pages and persist only provenance-validated chunks."""
        await self._advance_stage(message, IngestionStage.CHUNKING)
        page_inputs = [
            PageInput(
                page_number=page.page_number,
                text=page.text,
                ocr_engine=page.ocr_engine,
                ocr_confidence=page.ocr_confidence,
            )
            for page in parsed.pages
        ]
        drafts = chunk_pages(
            page_inputs,
            max_chars=self._chunk_max_chars,
            overlap=self._chunk_overlap_chars,
        )
        texts_by_page = {page.page_number: page.text for page in page_inputs}
        try:
            for draft in drafts:
                validate_chunk_provenance(draft, texts_by_page[draft.page_number])
        except ProvenanceError as error:
            # A provenance failure is a chunker bug, not bad input; never
            # persist anything from this run.
            raise PermanentIngestionError(f"chunk provenance invalid: {error}") from error

        async with session_scope(self._factory) as session:
            await bind_workspace(session, message.workspace_id)
            await session.execute(
                delete(Chunk).where(Chunk.document_version_id == content.version_id)
            )
            for index, draft in enumerate(drafts):
                session.add(
                    Chunk(
                        workspace_id=message.workspace_id,
                        document_version_id=content.version_id,
                        chunk_index=index,
                        content=draft.content,
                        content_hash=draft.content_hash,
                        token_count=draft.token_count,
                        page_number=draft.page_number,
                        section=draft.section,
                        char_start=draft.char_start,
                        char_end=draft.char_end,
                        language=draft.language,
                        ocr_engine=draft.ocr_engine,
                        ocr_confidence=draft.ocr_confidence,
                    )
                )
        logger.info(
            "chunked document",
            extra={"job_id": str(message.job_id), "chunks": len(drafts)},
        )

    async def _finish(self, message: JobMessage) -> None:
        async with session_scope(self._factory) as session:
            await bind_workspace(session, message.workspace_id)
            job = await session.get(IngestionJob, message.job_id)
            assert job is not None  # noqa: S101 - claimed above
            job.status = IngestionStatus.SUCCEEDED
            job.stage = IngestionStage.READY
            job.finished_at = datetime.now(UTC)
            document = await session.get(Document, job.document_id)
            if document is not None:
                document.status = DocumentStatus.READY
            await AuditLogRepository(session).record(
                action="document.ready",
                resource_type="document",
                resource_id=job.document_id,
                workspace_id=message.workspace_id,
            )
        logger.info("ingestion succeeded", extra={"job_id": str(message.job_id)})

    async def _quarantine(self, message: JobMessage, reason: str) -> None:
        async with session_scope(self._factory) as session:
            await bind_workspace(session, message.workspace_id)
            job = await session.get(IngestionJob, message.job_id)
            assert job is not None  # noqa: S101 - claimed above
            job.status = IngestionStatus.FAILED
            job.error = f"quarantined: {reason}"
            job.finished_at = datetime.now(UTC)
            document = await session.get(Document, job.document_id)
            if document is not None:
                document.status = DocumentStatus.QUARANTINED
            await AuditLogRepository(session).record(
                action="document.quarantined",
                resource_type="document",
                resource_id=job.document_id,
                workspace_id=message.workspace_id,
                detail={"reason": reason},
            )
        logger.warning(
            "document quarantined",
            extra={"job_id": str(message.job_id), "reason": reason},
        )

    async def _fail(self, message: JobMessage, error: str, *, retry: bool) -> None:
        async with session_scope(self._factory) as session:
            await bind_workspace(session, message.workspace_id)
            job = await session.get(IngestionJob, message.job_id)
            assert job is not None  # noqa: S101 - claimed above
            job.error = error
            will_retry = retry and job.attempts < self._max_attempts
            if will_retry:
                job.status = IngestionStatus.QUEUED
            else:
                job.status = IngestionStatus.FAILED
                job.finished_at = datetime.now(UTC)
                document = await session.get(Document, job.document_id)
                if document is not None:
                    document.status = DocumentStatus.FAILED
                await AuditLogRepository(session).record(
                    action="document.ingestion_failed",
                    resource_type="document",
                    resource_id=job.document_id,
                    workspace_id=message.workspace_id,
                    detail={"error": error},
                )
        if will_retry:
            await self._queue.enqueue(message)
            logger.warning(
                "ingestion attempt failed; requeued",
                extra={"job_id": str(message.job_id), "error": error},
            )
        else:
            await self._queue.dead_letter(message)
            logger.error(
                "ingestion failed terminally",
                extra={"job_id": str(message.job_id), "error": error},
            )

    async def requeue_stale(self) -> int:
        """Re-enqueue crashed (stale RUNNING) and orphaned (stale QUEUED) jobs.

        Duplicate messages are harmless because claiming is a compare-and-set.
        """
        cutoff = datetime.now(UTC) - self._stale_after
        async with session_scope(self._factory) as session:
            stale_running = (
                await session.scalars(
                    select(IngestionJob).where(
                        IngestionJob.status == IngestionStatus.RUNNING,
                        IngestionJob.started_at < cutoff,
                    )
                )
            ).all()
            stale_queued = (
                await session.scalars(
                    select(IngestionJob).where(
                        IngestionJob.status == IngestionStatus.QUEUED,
                        IngestionJob.updated_at < cutoff,
                    )
                )
            ).all()
            messages = []
            for job in stale_running:
                job.status = IngestionStatus.QUEUED
                messages.append(JobMessage(job_id=job.id, workspace_id=job.workspace_id))
            for job in stale_queued:
                job.updated_at = datetime.now(UTC)
                messages.append(JobMessage(job_id=job.id, workspace_id=job.workspace_id))
        for queue_message in messages:
            await self._queue.enqueue(queue_message)
            logger.warning(
                "stale job requeued",
                extra={"job_id": str(queue_message.job_id)},
            )
        return len(messages)

    async def run_forever(self, *, idle_timeout_seconds: float = 5.0) -> None:
        """Consume jobs until cancelled, periodically recovering stale ones."""
        while True:
            worked = await self.process_next(idle_timeout_seconds)
            if not worked:
                await self.requeue_stale()


def _main() -> None:
    from app.config import get_settings
    from app.db.session import get_session_factory
    from app.ingestion.queue import RedisJobQueue
    from app.ingestion.scanner import SignatureScanner
    from app.parsing.ocr import build_ocr_engine
    from app.storage.s3 import S3ObjectStorage

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    worker = IngestionWorker(
        session_factory=get_session_factory(),
        storage=S3ObjectStorage(settings),
        queue=RedisJobQueue(
            settings.redis_url,
            queue_key=settings.ingestion_queue_key,
            dead_letter_key=settings.ingestion_dead_letter_key,
        ),
        scanner=SignatureScanner(),
        ocr_engine=build_ocr_engine(settings.ocr_engine, settings.ocr_languages),
        store_page_images=settings.ingestion_store_page_images,
        chunk_max_chars=settings.chunk_max_chars,
        chunk_overlap_chars=settings.chunk_overlap_chars,
        max_attempts=settings.ingestion_max_attempts,
        stale_after_seconds=settings.ingestion_stale_after_seconds,
    )
    asyncio.run(worker.run_forever())


if __name__ == "__main__":
    _main()
