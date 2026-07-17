"""Heading-, paragraph-, and table-aware chunking.

The cardinal rule: a chunk's content is always the exact substring
`page_text[char_start:char_end]` — the chunker computes boundaries, it never
rewrites text. That is what makes provenance mechanically verifiable (see
`app.chunking.provenance`) and citations reproducible later in the pipeline.

Structure handling:
- Markdown headings (`#`..`######`) and short numbered headings ("2.1 Scope")
  update the section path; the heading line itself starts the next chunk.
- Blank-line-separated paragraphs are packed greedily up to `max_chars`.
- Pipe tables are kept atomic — a table is never split mid-row, even when it
  exceeds `max_chars` (practical table awareness, not table parsing).
- Oversized paragraphs split at whitespace with `overlap` characters carried
  between windows; packed chunks also extend backwards by `overlap` so
  neighboring chunks share context.

Chunks never span pages: offsets are page-relative and a page reference is
part of provenance.
"""

import hashlib
import re
from dataclasses import dataclass

_HEADING_MD = re.compile(r"^(#{1,6})\s+\S")
_HEADING_NUMBERED = re.compile(r"^\d+(\.\d+)*[.)]?\s+\S.{0,79}$")
_TOKEN = re.compile(r"\S+")

MAX_SECTION_LENGTH = 500


@dataclass(frozen=True)
class PageInput:
    """One source page as parsed/OCR'd upstream."""

    page_number: int
    text: str
    language: str | None = None
    ocr_engine: str | None = None
    ocr_confidence: float | None = None


@dataclass(frozen=True)
class ChunkDraft:
    """A chunk candidate with full provenance, not yet persisted."""

    content: str
    content_hash: str
    page_number: int
    section: str | None
    char_start: int
    char_end: int
    token_count: int
    language: str | None
    ocr_engine: str | None
    ocr_confidence: float | None


@dataclass(frozen=True)
class _Block:
    kind: str  # "heading" | "paragraph" | "table"
    start: int
    end: int
    heading_level: int = 0


def count_tokens(text: str) -> int:
    """Whitespace-delimited token count — a documented approximation."""
    return len(_TOKEN.findall(text))


def _split_blocks(text: str) -> list[_Block]:
    """Blank-line-separated blocks with exact offsets into `text`."""
    blocks: list[_Block] = []
    position = 0
    length = len(text)
    while position < length:
        # Skip blank space between blocks.
        while position < length and text[position] in "\r\n \t":
            position += 1
        if position >= length:
            break
        match = re.compile(r"\n\s*\n").search(text, position)
        end = match.start() if match else length
        while end > position and text[end - 1] in "\r\n \t":
            end -= 1
        raw = text[position:end]
        blocks.append(_classify_block(raw, position, end))
        position = end
    return blocks


def _classify_block(raw: str, start: int, end: int) -> _Block:
    lines = raw.splitlines()
    heading = _HEADING_MD.match(raw)
    if heading and len(lines) == 1:
        return _Block(kind="heading", start=start, end=end, heading_level=len(heading.group(1)))
    if len(lines) == 1 and _HEADING_NUMBERED.match(raw.strip()):
        depth = raw.strip().split()[0].rstrip(".)").count(".") + 1
        return _Block(kind="heading", start=start, end=end, heading_level=depth)
    piped = sum(1 for line in lines if line.count("|") >= 2)
    if lines and piped / len(lines) > 0.5:
        return _Block(kind="table", start=start, end=end)
    return _Block(kind="paragraph", start=start, end=end)


def _heading_text(text: str, block: _Block) -> str:
    raw = text[block.start : block.end].strip()
    return raw.lstrip("#").strip()


def _snap_to_whitespace(text: str, position: int, *, lower_bound: int) -> int:
    """Move a boundary left to the nearest whitespace so words stay whole."""
    while position > lower_bound and position < len(text) and not text[position - 1].isspace():
        position -= 1
    return position


def _split_oversized(
    text: str,
    start: int,
    end: int,
    max_chars: int,
    overlap: int,
) -> list[tuple[int, int]]:
    """Windows over one oversized block, each ≤ max_chars, overlapping."""
    spans: list[tuple[int, int]] = []
    window_start = start
    while window_start < end:
        window_end = min(window_start + max_chars, end)
        if window_end < end:
            snapped = _snap_to_whitespace(text, window_end, lower_bound=window_start + 1)
            if snapped > window_start:
                window_end = snapped
        spans.append((window_start, window_end))
        if window_end >= end:
            break
        next_start = max(window_end - overlap, window_start + 1)
        window_start = _snap_to_whitespace(text, next_start, lower_bound=window_start + 1)
    return spans


def chunk_page(
    page: PageInput,
    *,
    max_chars: int = 1200,
    overlap: int = 150,
    initial_section: list[str] | None = None,
) -> tuple[list[ChunkDraft], list[str]]:
    """Chunk one page; returns drafts plus the section path carried forward."""
    if max_chars < 1 or overlap < 0 or overlap >= max_chars:
        msg = "chunking requires max_chars >= 1 and 0 <= overlap < max_chars"
        raise ValueError(msg)
    text = page.text
    section_path: list[str] = list(initial_section or [])
    drafts: list[ChunkDraft] = []
    pending: list[_Block] = []
    pending_section = _render_section(section_path)
    last_start, last_end = -1, 0

    def flush() -> None:
        nonlocal pending, last_start, last_end
        if not pending:
            return
        group_start, group_end = pending[0].start, pending[-1].end
        if len(pending) == 1 and pending[0].kind == "table":
            # Practical table awareness: a table is atomic, whatever its size.
            spans = [(group_start, group_end)]
        elif group_end - group_start > max_chars and len(pending) == 1:
            spans = _split_oversized(text, group_start, group_end, max_chars, overlap)
        else:
            spans = _pack_spans(pending, max_chars, text)
        for span_start, span_end in spans:
            # Widen non-overlapping successors backwards so neighbors share
            # context; the widened span is still an exact source substring.
            if overlap and drafts and span_start >= last_end > 0:
                widened = _snap_to_whitespace(text, max(span_start - overlap, 0), lower_bound=0)
                span_start = max(widened, last_start + 1)
            drafts.append(_draft(page, text, span_start, span_end, pending_section))
            last_start, last_end = span_start, span_end
        pending = []

    for block in _split_blocks(text):
        if block.kind == "heading":
            flush()
            level = block.heading_level
            del section_path[level - 1 :]
            section_path.append(_heading_text(text, block))
            pending_section = _render_section(section_path)
            pending.append(block)
        elif block.kind == "table":
            flush()
            pending = [block]
            flush()
        else:
            projected = block.end - (pending[0].start if pending else block.start)
            if pending and projected > max_chars:
                flush()
                pending_section = _render_section(section_path)
            pending.append(block)
    flush()
    return drafts, section_path


def _pack_spans(
    blocks: list[_Block],
    max_chars: int,
    text: str,
) -> list[tuple[int, int]]:
    """Greedily pack a pending group of blocks into spans of at most max_chars."""
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(blocks):
        start = blocks[index].start
        end = blocks[index].end
        next_index = index + 1
        while next_index < len(blocks) and blocks[next_index].end - start <= max_chars:
            end = blocks[next_index].end
            next_index += 1
        if end - start > max_chars:
            spans.extend(_split_oversized(text, start, end, max_chars, overlap=0))
        else:
            spans.append((start, end))
        index = next_index
    return spans


def _render_section(path: list[str]) -> str | None:
    if not path:
        return None
    return " > ".join(path)[:MAX_SECTION_LENGTH]


def _draft(
    page: PageInput,
    text: str,
    start: int,
    end: int,
    section: str | None,
) -> ChunkDraft:
    content = text[start:end]
    return ChunkDraft(
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        page_number=page.page_number,
        section=section,
        char_start=start,
        char_end=end,
        token_count=count_tokens(content),
        language=page.language,
        ocr_engine=page.ocr_engine,
        ocr_confidence=page.ocr_confidence,
    )


def chunk_pages(
    pages: list[PageInput],
    *,
    max_chars: int = 1200,
    overlap: int = 150,
) -> list[ChunkDraft]:
    """Chunk every page, carrying the heading hierarchy across page breaks."""
    drafts: list[ChunkDraft] = []
    section: list[str] = []
    for page in pages:
        page_drafts, section = chunk_page(
            page,
            max_chars=max_chars,
            overlap=overlap,
            initial_section=section,
        )
        drafts.extend(page_drafts)
    return drafts
