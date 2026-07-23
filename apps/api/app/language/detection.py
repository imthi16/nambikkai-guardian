"""Query-language detection for Tamil, English, and Tanglish.

Detection is deterministic and explainable. It first measures the ratio of
Tamil-script to Latin-script letters, then disambiguates Latin-only text into
English vs. Tanglish (romanized Tamil) using a small, auditable marker
lexicon. Confidence is derived from script purity and marker evidence, and
every low-signal case records a `limitation` string rather than guessing
silently.

Treat the result as untrusted metadata: it informs retrieval, it never becomes
an instruction to the model.
"""

from __future__ import annotations

import re
import unicodedata

from app.language.normalization import normalize_text
from app.language.types import Language, LanguageDetection, ScriptProfile

_TAMIL_BLOCK = (0x0B80, 0x0BFF)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# Common romanized-Tamil tokens. Presence of these in Latin-only text is strong
# evidence of Tanglish. Kept small and auditable on purpose; the spelling and
# transliteration adapters own the exhaustive vocabulary.
_TANGLISH_MARKERS = frozenset(
    {
        "vanakkam",
        "enna",
        "eppadi",
        "epdi",
        "iruku",
        "irukku",
        "panna",
        "pannu",
        "pannunga",
        "seri",
        "illa",
        "illai",
        "aama",
        "aamaa",
        "romba",
        "konjam",
        "yaaru",
        "yenna",
        "enga",
        "epo",
        "eppo",
        "nandri",
        "thevai",
        "venum",
        "vendum",
        "kandippa",
        "sollunga",
        "puriyala",
        "theriyuma",
        "theriyum",
        "namba",
    }
)

# Short function words that appear in both English and Tanglish; they must not
# by themselves swing detection either way.
_AMBIGUOUS = frozenset({"a", "i", "na", "no", "ok", "am", "an", "en"})


def _script_profile(text: str) -> ScriptProfile:
    tamil = 0
    latin = 0
    for char in text:
        if not char.isalpha():
            continue
        code = ord(char)
        if _TAMIL_BLOCK[0] <= code <= _TAMIL_BLOCK[1]:
            tamil += 1
        elif char.isascii() or "LATIN" in _char_name(char):
            latin += 1
    letters = tamil + latin
    if letters == 0:
        return ScriptProfile(tamil_ratio=0.0, latin_ratio=0.0, letter_count=0)
    return ScriptProfile(
        tamil_ratio=tamil / letters,
        latin_ratio=latin / letters,
        letter_count=letters,
    )


def _char_name(char: str) -> str:
    try:
        return unicodedata.name(char)
    except ValueError:
        return ""


def _latin_words(text: str) -> list[str]:
    return [word.casefold() for word in _WORD.findall(text) if word.isascii()]


def _tanglish_evidence(text: str) -> float:
    words = [word for word in _latin_words(text) if word not in _AMBIGUOUS]
    if not words:
        return 0.0
    hits = sum(1 for word in words if word in _TANGLISH_MARKERS)
    return hits / len(words)


def detect_language(text: str) -> LanguageDetection:
    """Classify `text` as Tamil, English, Tanglish, or unknown.

    Uses the normalized form so punctuation and width variants do not skew the
    ratios, while the reported profile still reflects real letters only.
    """
    normalized = normalize_text(text)
    profile = _script_profile(normalized)
    limitations: list[str] = []

    if not profile.has_letters:
        limitations.append("no alphabetic characters to classify")
        return LanguageDetection(Language.UNKNOWN, 0.0, profile, tuple(limitations))

    if profile.letter_count < 3:
        limitations.append("very short input; detection is low-confidence")

    # Mixed script: meaningful Tamil and Latin together.
    if profile.tamil_ratio >= 0.15 and profile.latin_ratio >= 0.15:
        marker_ratio = _tanglish_evidence(normalized)
        # Tamil script present alongside Latin usually means a bilingual query;
        # we report the dominant script but flag the mix.
        limitations.append("mixed Tamil and Latin script")
        if profile.tamil_ratio >= profile.latin_ratio:
            confidence = 0.55 + 0.4 * profile.tamil_ratio
            return LanguageDetection(
                Language.TAMIL, min(confidence, 0.95), profile, tuple(limitations)
            )
        language = Language.TANGLISH if marker_ratio > 0 else Language.ENGLISH
        confidence = 0.5 + 0.3 * profile.latin_ratio
        return LanguageDetection(language, min(confidence, 0.9), profile, tuple(limitations))

    if profile.tamil_ratio >= 0.85:
        confidence = 0.7 + 0.3 * profile.tamil_ratio
        return LanguageDetection(Language.TAMIL, min(confidence, 0.99), profile, tuple(limitations))

    # Latin-dominant: decide English vs Tanglish from marker evidence.
    marker_ratio = _tanglish_evidence(normalized)
    if marker_ratio >= 0.4:
        confidence = min(0.6 + marker_ratio, 0.95)
        return LanguageDetection(Language.TANGLISH, confidence, profile, tuple(limitations))
    if marker_ratio > 0.0:
        limitations.append("ambiguous romanized text; could be English or Tanglish")
        return LanguageDetection(Language.TANGLISH, 0.5 + marker_ratio, profile, tuple(limitations))

    confidence = 0.6 + 0.3 * profile.latin_ratio
    if profile.letter_count < 3:
        confidence = min(confidence, 0.5)
    return LanguageDetection(Language.ENGLISH, min(confidence, 0.95), profile, tuple(limitations))
