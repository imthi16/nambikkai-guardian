"""The query-processing pipeline: detect, normalize, transliterate, expand.

`QueryProcessor.process` is the single entry point the retrieval stage calls.
It always retains the user's exact input, produces a canonical `normalized`
form, and — for romanized Tamil — a Tamil-script `transliterated` form plus
extra search variants so Tanglish queries can match Tamil-script documents.

Providers (transliteration, spelling) are injected behind protocols so they
can be swapped without touching this orchestration.
"""

from __future__ import annotations

from app.language.detection import detect_language
from app.language.normalization import normalize_text
from app.language.spelling import SpellingNormalizer, get_default_spelling_normalizer
from app.language.transliteration import Transliterator, get_default_transliterator
from app.language.types import Language, ProcessedQuery


class QueryProcessor:
    """Turns raw query text into a fully-represented `ProcessedQuery`."""

    def __init__(
        self,
        *,
        transliterator: Transliterator | None = None,
        spelling_normalizer: SpellingNormalizer | None = None,
    ) -> None:
        self._transliterator = transliterator or get_default_transliterator()
        self._spelling = spelling_normalizer or get_default_spelling_normalizer()

    def process(self, text: str) -> ProcessedQuery:
        normalized = normalize_text(text)
        detection = detect_language(text)

        if detection.language is Language.TANGLISH:
            spell_corrected = self._spelling.normalize(normalized)
            transliterated = self._transliterator.transliterate(spell_corrected)
            expansions: tuple[str, ...] = ()
            if spell_corrected != normalized:
                # Keep the spelling-corrected romanized form as an extra lexical
                # candidate alongside the Tamil-script transliteration.
                expansions = (spell_corrected,)
        elif detection.language is Language.TAMIL:
            transliterated = normalized
            expansions = ()
        else:
            # English or unknown: transliteration would only add noise.
            transliterated = normalized
            expansions = ()

        return ProcessedQuery(
            original=text,
            normalized=normalized,
            transliterated=transliterated,
            detection=detection,
            expansions=expansions,
        )


def get_default_query_processor() -> QueryProcessor:
    """A processor wired with the default MVP providers."""
    return QueryProcessor()
