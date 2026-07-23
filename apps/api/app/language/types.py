"""Shared language-processing data shapes.

Every query keeps three representations so retrieval never loses the user's
intent: the exact `original` text, a `normalized` form (Unicode- and
punctuation-canonical), and a `transliterated` form (Tanglish rendered in
Tamil script). Detection is advisory metadata, never an instruction, and its
`limitations` list is surfaced so downstream stages can widen retrieval when
confidence is low.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Language(StrEnum):
    """Languages the platform detects at the query and chunk level."""

    TAMIL = "tam"
    ENGLISH = "eng"
    TANGLISH = "tanglish"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ScriptProfile:
    """Character-class ratios over a string; the basis for detection.

    Ratios are computed over *letters* only (digits, punctuation, and
    whitespace are excluded) so that "Order #12 ready?" is treated as English
    rather than diluted toward unknown.
    """

    tamil_ratio: float
    latin_ratio: float
    letter_count: int

    @property
    def has_letters(self) -> bool:
        return self.letter_count > 0


@dataclass(frozen=True)
class LanguageDetection:
    """The detected language with a calibrated confidence and its evidence."""

    language: Language
    confidence: float
    script: ScriptProfile
    limitations: tuple[str, ...] = ()

    def as_metadata(self) -> dict[str, object]:
        """A JSON-safe view for telemetry and message metadata."""
        return {
            "language": self.language.value,
            "confidence": round(self.confidence, 4),
            "tamil_ratio": round(self.script.tamil_ratio, 4),
            "latin_ratio": round(self.script.latin_ratio, 4),
            "letter_count": self.script.letter_count,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class ProcessedQuery:
    """A user query with every representation retrieval may need.

    `original` is always retained verbatim. `normalized` is safe to index and
    match on. `transliterated` is populated when a Latin-script Tamil query is
    rendered into Tamil script; otherwise it repeats `normalized` so callers
    always have a usable Tamil-script candidate.
    """

    original: str
    normalized: str
    transliterated: str
    detection: LanguageDetection
    expansions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def search_variants(self) -> tuple[str, ...]:
        """De-duplicated, order-preserving candidates for hybrid retrieval."""
        seen: dict[str, None] = {}
        for candidate in (self.normalized, self.transliterated, *self.expansions):
            key = candidate.strip()
            if key and key not in seen:
                seen[key] = None
        return tuple(seen)

    def as_metadata(self) -> dict[str, object]:
        return {
            "original": self.original,
            "normalized": self.normalized,
            "transliterated": self.transliterated,
            "expansions": list(self.expansions),
            "detection": self.detection.as_metadata(),
        }
