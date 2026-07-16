"""OCR engines behind a replaceable adapter.

The pipeline depends only on `OcrEngine`; which engine runs is configuration
(`OCR_ENGINE`). `TesseractOcrEngine` covers Tamil and English via the system
`tesseract` binary with `tam`/`eng` models. The planned PaddleOCR adapter
implements the same protocol when it lands. `NullOcrEngine` keeps the
pipeline functional (with an explicit provenance marker) where no engine is
installed.
"""

import asyncio
from typing import Any, Protocol

from app.parsing.types import OcrBlock, OcrResult


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


def build_ocr_engine(engine: str, languages: str) -> OcrEngine:
    """Resolve the configured engine name to an adapter."""
    if engine == "tesseract":
        return TesseractOcrEngine(languages)
    return NullOcrEngine()
