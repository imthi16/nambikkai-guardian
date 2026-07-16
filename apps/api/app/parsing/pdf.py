"""PDF parsing: digital extraction with a fallback chain, plus rasterization.

`pypdf` is the primary extractor; when it cannot open or read the file,
`pypdfium2` (a PDFium binding) is the fallback. A page whose extracted text
is effectively empty is flagged `needs_ocr` — that heuristic is what routes
scanned pages to the OCR adapter. Rasterization for OCR and page-image
references also comes from PDFium.
"""

import io
import logging

import pypdfium2
from pypdf import PdfReader

from app.parsing.types import ParsedDocument, ParsedPage, ParserError

logger = logging.getLogger("app.parsing")

# Below this many non-whitespace characters a page is treated as scanned.
SCANNED_TEXT_THRESHOLD = 24
_RENDER_SCALE = 2.0  # ~144 dpi; enough for OCR without huge images


def _flag_scanned(pages: list[ParsedPage]) -> list[ParsedPage]:
    for page in pages:
        if len("".join(page.text.split())) < SCANNED_TEXT_THRESHOLD:
            page.needs_ocr = True
    return pages


def _parse_with_pypdf(data: bytes) -> list[ParsedPage]:
    reader = PdfReader(io.BytesIO(data), strict=False)
    return [
        ParsedPage(page_number=index + 1, text=page.extract_text() or "")
        for index, page in enumerate(reader.pages)
    ]


def _parse_with_pdfium(data: bytes) -> list[ParsedPage]:
    document = pypdfium2.PdfDocument(data)
    try:
        pages: list[ParsedPage] = []
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                pages.append(ParsedPage(page_number=index + 1, text=textpage.get_text_bounded()))
            finally:
                textpage.close()
                page.close()
        return pages
    finally:
        document.close()


def parse_pdf(data: bytes) -> ParsedDocument:
    """Extract digital text page by page; fall back to PDFium; flag scanned pages."""
    try:
        pages = _parse_with_pypdf(data)
        if pages:
            return ParsedDocument(pages=_flag_scanned(pages), parser="pypdf")
        logger.warning("pypdf returned no pages; falling back to pdfium")
    except Exception as error:  # noqa: BLE001 - any pypdf failure triggers the fallback
        logger.warning("pypdf failed (%s); falling back to pdfium", type(error).__name__)
    try:
        pages = _parse_with_pdfium(data)
    except Exception as error:
        msg = "the PDF could not be parsed by pypdf or pdfium"
        raise ParserError(msg) from error
    if not pages:
        msg = "the PDF contains no pages"
        raise ParserError(msg)
    return ParsedDocument(pages=_flag_scanned(pages), parser="pdfium")


def render_pdf_page_png(data: bytes, page_number: int) -> bytes:
    """Rasterize one 1-indexed page to PNG for OCR or page-image references."""
    document = pypdfium2.PdfDocument(data)
    try:
        page = document[page_number - 1]
        try:
            bitmap = page.render(scale=_RENDER_SCALE)
            image = bitmap.to_pil()
        finally:
            page.close()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except ParserError:
        raise
    except Exception as error:
        msg = f"page {page_number} could not be rendered"
        raise ParserError(msg) from error
    finally:
        document.close()
