"""Immutable provenance validation — the gate in front of chunk persistence.

Downstream citation and verification depend on every chunk being traceable
to an exact span of source text. This validator re-derives what can be
re-derived (substring equality, hash, token count) and rejects anything
inconsistent; the worker never persists a chunk that fails it.
"""

import hashlib

from app.chunking.chunker import MAX_SECTION_LENGTH, ChunkDraft, count_tokens


class ProvenanceError(Exception):
    """The chunk's provenance is missing, inconsistent, or tampered with."""


def validate_chunk_provenance(draft: ChunkDraft, page_text: str) -> None:
    """Raise `ProvenanceError` unless every provenance claim checks out."""
    if not draft.content or not draft.content.strip():
        msg = "chunk content is empty"
        raise ProvenanceError(msg)
    if draft.page_number < 1:
        msg = "chunk page number must be positive"
        raise ProvenanceError(msg)
    if not (0 <= draft.char_start < draft.char_end <= len(page_text)):
        msg = "chunk character span is out of bounds"
        raise ProvenanceError(msg)
    if page_text[draft.char_start : draft.char_end] != draft.content:
        msg = "chunk content does not match its source span"
        raise ProvenanceError(msg)
    if hashlib.sha256(draft.content.encode("utf-8")).hexdigest() != draft.content_hash:
        msg = "chunk content hash is wrong"
        raise ProvenanceError(msg)
    if draft.token_count != count_tokens(draft.content) or draft.token_count < 1:
        msg = "chunk token count is wrong"
        raise ProvenanceError(msg)
    if draft.section is not None and (
        not draft.section.strip() or len(draft.section) > MAX_SECTION_LENGTH
    ):
        msg = "chunk section label is invalid"
        raise ProvenanceError(msg)
    if draft.ocr_confidence is not None and not (0.0 <= draft.ocr_confidence <= 1.0):
        msg = "chunk OCR confidence is out of range"
        raise ProvenanceError(msg)
