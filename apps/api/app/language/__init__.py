"""Language detection and normalization for Tamil, English, and Tanglish."""

from app.language.detection import detect_language
from app.language.normalization import normalize_for_match, normalize_text
from app.language.processor import QueryProcessor, get_default_query_processor
from app.language.types import (
    Language,
    LanguageDetection,
    ProcessedQuery,
    ScriptProfile,
)

__all__ = [
    "Language",
    "LanguageDetection",
    "ProcessedQuery",
    "QueryProcessor",
    "ScriptProfile",
    "detect_language",
    "get_default_query_processor",
    "normalize_for_match",
    "normalize_text",
]
