"""OCR adapter behavior; the real Tesseract test runs where the binary exists."""

import io
import shutil

import pytest
from app.parsing.ocr import (
    NullOcrEngine,
    PaddleOcrEngine,
    TesseractOcrEngine,
    build_ocr_engine,
    to_paddle_language,
)


async def test_null_engine_never_invents_text() -> None:
    result = await NullOcrEngine().recognize(b"png-bytes-irrelevant")
    assert result.text == ""
    assert result.confidence is None
    assert result.blocks == []


def test_engine_factory() -> None:
    assert isinstance(build_ocr_engine("none", "tam+eng"), NullOcrEngine)
    tesseract = build_ocr_engine("tesseract", "tam+eng")
    assert isinstance(tesseract, TesseractOcrEngine)
    assert tesseract.name == "tesseract"
    paddle = build_ocr_engine("paddle", "tam+eng")
    assert isinstance(paddle, PaddleOcrEngine)
    assert paddle.name == "paddleocr"


@pytest.mark.parametrize(
    ("languages", "expected"),
    [
        ("tam+eng", "ta"),
        ("eng+tam", "en"),
        ("eng", "en"),
        ("ta", "ta"),
        ("unknown", "en"),
        ("", "en"),
    ],
)
def test_paddle_language_mapping(languages: str, expected: str) -> None:
    assert to_paddle_language(languages) == expected


class FakeReader:
    """Stands in for a loaded PaddleOCR instance without the native wheel."""

    def __init__(self, result: object) -> None:
        self._result = result
        self.calls = 0

    def ocr(self, image: object, cls: bool = True) -> object:  # noqa: FBT001, FBT002, ARG002
        self.calls += 1
        return self._result


async def test_paddle_parses_lines_into_blocks_with_bbox() -> None:
    raw = [
        [
            [[[10, 20], [300, 20], [300, 60], [10, 60]], ("வணக்கம்", 0.98)],
            [[[10, 70], [200, 70], [200, 110], [10, 110]], ("world", 0.90)],
        ]
    ]
    engine = PaddleOcrEngine("tam+eng", reader=FakeReader(raw))
    _white_png = _tiny_png()

    result = await engine.recognize(_white_png)

    assert result.text == "வணக்கம் world"
    assert result.confidence == pytest.approx((0.98 + 0.90) / 2)
    assert [block.text for block in result.blocks] == ["வணக்கம்", "world"]
    assert result.blocks[0].bbox == (10, 20, 290, 40)
    assert result.blocks[0].confidence == pytest.approx(0.98)


async def test_paddle_handles_empty_page() -> None:
    engine = PaddleOcrEngine("eng", reader=FakeReader([None]))
    result = await engine.recognize(_tiny_png())
    assert result.text == ""
    assert result.confidence is None
    assert result.blocks == []


async def test_paddle_skips_malformed_lines_untrusted_output() -> None:
    raw = [
        [
            "not-a-line",
            [[[0, 0], [1, 0], [1, 1], [0, 1]], ("", 0.99)],
            [[[5, 5], [15, 5], [15, 25], [5, 25]], ("keep", 0.8)],
        ]
    ]
    engine = PaddleOcrEngine("eng", reader=FakeReader(raw))
    result = await engine.recognize(_tiny_png())
    assert result.text == "keep"
    assert len(result.blocks) == 1


def _tiny_png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract binary not installed")
async def test_tesseract_recognizes_drawn_english_text() -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 60), "GUARDIAN EVIDENCE 2026", fill="black", font_size=64)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    result = await TesseractOcrEngine("eng").recognize(buffer.getvalue())
    assert "GUARDIAN" in result.text.upper()
    assert result.confidence is not None and 0 < result.confidence <= 1
    assert result.blocks, "word-level blocks with bounding boxes expected"
    first = result.blocks[0]
    assert first.bbox is not None and len(first.bbox) == 4
