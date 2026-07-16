"""Shared parsing data shapes; provenance is carried, never reconstructed."""

from dataclasses import dataclass, field


class ParserError(Exception):
    """The document could not be parsed by any available parser."""


@dataclass(frozen=True)
class OcrBlock:
    """One recognized text region; bbox is (left, top, width, height) pixels."""

    text: str
    confidence: float | None
    bbox: tuple[int, int, int, int] | None

    def as_provenance(self) -> dict[str, object]:
        return {"text": self.text, "confidence": self.confidence, "bbox": self.bbox}


@dataclass(frozen=True)
class OcrResult:
    """What one OCR pass over one page image produced."""

    text: str
    confidence: float | None
    blocks: list[OcrBlock] = field(default_factory=list)


@dataclass
class ParsedPage:
    """One page of extracted content with its extraction provenance."""

    page_number: int
    text: str
    needs_ocr: bool = False
    ocr_engine: str | None = None
    ocr_confidence: float | None = None
    ocr_blocks: list[OcrBlock] | None = None
    image_storage_key: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    """Every page of one document version, in page order."""

    pages: list[ParsedPage]
    parser: str
