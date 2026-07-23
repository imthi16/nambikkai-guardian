"""OCR engines behind a replaceable adapter.

The pipeline depends only on `OcrEngine`; which engine runs is configuration
(`OCR_ENGINE`). `TesseractOcrEngine` covers Tamil and English via the system
`tesseract` binary with `tam`/`eng` models. `PaddleOcrEngine` covers the same
languages via the PaddleOCR models (the project's target OCR stack) and is
imported lazily so the heavy dependency is only required when it is selected.
`NullOcrEngine` keeps the pipeline functional (with an explicit provenance
marker) where no engine is installed.
"""

import asyncio
from typing import Any, Protocol

from app.parsing.types import OcrBlock, OcrResult

# Our OCR language configuration uses Tesseract-style codes joined with '+'
# (e.g. "tam+eng"). PaddleOCR instead selects a single language model per
# instance using its own codes, so we map and pick the primary language.
_PADDLE_LANGUAGE_CODES = {
    "tam": "ta",
    "ta": "ta",
    "eng": "en",
    "en": "en",
}


def to_paddle_language(languages: str) -> str:
    """Map a Tesseract-style language string to a single PaddleOCR code.

    PaddleOCR loads one recognition model per instance, so when several
    languages are requested (e.g. "tam+eng") the first recognised code wins
    and the rest are ignored. Unknown codes fall back to English.
    """
    for token in languages.split("+"):
        code = _PADDLE_LANGUAGE_CODES.get(token.strip().lower())
        if code is not None:
            return code
    return "en"


def _bbox_from_polygon(polygon: Any) -> tuple[int, int, int, int] | None:
    """Reduce a PaddleOCR quadrilateral to an (left, top, width, height) box.

    PaddleOCR returns four [x, y] corner points; downstream provenance stores
    axis-aligned boxes, so we take the enclosing rectangle.
    """
    try:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or not ys:
        return None
    left, top = min(xs), min(ys)
    return (int(left), int(top), int(max(xs) - left), int(max(ys) - top))


class OcrEngine(Protocol):
    """Recognize text on one page image (PNG bytes)."""

    name: str

    async def recognize(self, image_png: bytes) -> OcrResult: ...


class NullOcrEngine:
    """Records that OCR was needed but unavailable; never invents text."""

    name = "unavailable"

    async def recognize(self, image_png: bytes) -> OcrResult:
        return OcrResult(text="", confidence=None, blocks=[])


class TesseractOcrEngine:
    """Tesseract adapter; `languages` uses tesseract codes, e.g. 'tam+eng'."""

    def __init__(self, languages: str = "tam+eng") -> None:
        self.name = "tesseract"
        self._languages = languages

    async def recognize(self, image_png: bytes) -> OcrResult:
        # pytesseract is synchronous; keep the event loop responsive.
        return await asyncio.to_thread(self._recognize_sync, image_png)

    def _recognize_sync(self, image_png: bytes) -> OcrResult:
        import io

        import pytesseract
        from PIL import Image

        image = Image.open(io.BytesIO(image_png))
        payload: dict[str, list[Any]] = pytesseract.image_to_data(
            image,
            lang=self._languages,
            output_type=pytesseract.Output.DICT,
        )
        blocks: list[OcrBlock] = []
        confidences: list[float] = []
        for text, conf, left, top, width, height in zip(
            payload["text"],
            payload["conf"],
            payload["left"],
            payload["top"],
            payload["width"],
            payload["height"],
            strict=True,
        ):
            token = str(text).strip()
            confidence = float(conf)
            if not token or confidence < 0:  # -1 marks structural rows
                continue
            blocks.append(
                OcrBlock(
                    text=token,
                    confidence=confidence / 100.0,
                    bbox=(int(left), int(top), int(width), int(height)),
                )
            )
            confidences.append(confidence / 100.0)
        joined = " ".join(block.text for block in blocks)
        overall = sum(confidences) / len(confidences) if confidences else None
        return OcrResult(text=joined, confidence=overall, blocks=blocks)


class PaddleOcrEngine:
    """PaddleOCR adapter for Tamil and English (the project's target stack).

    The `paddleocr` package pulls in large native wheels, so it is imported
    lazily the first time recognition runs. A prebuilt reader may be injected
    (`reader`) to make the adapter unit-testable without the dependency.
    """

    def __init__(self, languages: str = "tam+eng", *, reader: Any | None = None) -> None:
        self.name = "paddleocr"
        self._language = to_paddle_language(languages)
        self._reader = reader

    def _get_reader(self) -> Any:
        if self._reader is None:
            from paddleocr import PaddleOCR

            self._reader = PaddleOCR(use_angle_cls=True, lang=self._language, show_log=False)
        return self._reader

    async def recognize(self, image_png: bytes) -> OcrResult:
        # PaddleOCR is synchronous and CPU-heavy; keep the event loop free.
        return await asyncio.to_thread(self._recognize_sync, image_png)

    def _recognize_sync(self, image_png: bytes) -> OcrResult:
        import io

        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(image_png)).convert("RGB")
        reader = self._get_reader()
        raw = reader.ocr(np.asarray(image), cls=True)
        return self._parse_result(raw)

    @staticmethod
    def _parse_result(raw: Any) -> OcrResult:
        """Translate PaddleOCR's nested output into our provenance shape.

        Classic layout is ``[[[polygon, (text, score)], ...]]`` for one image;
        an empty page yields ``[None]`` or ``[[]]``. Anything unexpected is
        skipped rather than trusted, because OCR output is untrusted data.
        """
        blocks: list[OcrBlock] = []
        confidences: list[float] = []
        page = raw[0] if isinstance(raw, (list, tuple)) and raw else None
        for line in page or []:
            try:
                polygon, recognition = line[0], line[1]
                text = str(recognition[0]).strip()
                score = float(recognition[1])
            except (TypeError, ValueError, IndexError):
                continue
            if not text:
                continue
            blocks.append(OcrBlock(text=text, confidence=score, bbox=_bbox_from_polygon(polygon)))
            confidences.append(score)
        joined = " ".join(block.text for block in blocks)
        overall = sum(confidences) / len(confidences) if confidences else None
        return OcrResult(text=joined, confidence=overall, blocks=blocks)


def build_ocr_engine(engine: str, languages: str) -> OcrEngine:
    """Resolve the configured engine name to an adapter."""
    if engine == "tesseract":
        return TesseractOcrEngine(languages)
    if engine == "paddle":
        return PaddleOcrEngine(languages)
    return NullOcrEngine()
