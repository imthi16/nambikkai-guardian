"""Prompt-injection defence: detection, scoring, and quarantine decisions.

Uploaded files, OCR output, and retrieved chunks are *untrusted data*. This
package finds passages inside that data which try to act as *instructions* to
the assistant (direct overrides, system impersonation, indirect/hidden,
obfuscated, or encoded payloads), scores how injection-like they are, and turns
that into a deterministic quarantine decision.

The detector never executes, follows, or even decodes-and-acts-on the content;
it only measures it. Enforcement (never letting quarantined content reach
retrieval or generation) lives in the ingestion worker and the retrieval data
layer, which import :func:`assess_text` and :class:`InjectionScanner` from here.
"""

from app.safety.detector import (
    InjectionDetector,
    assess_text,
    get_default_detector,
)
from app.safety.scanner import DocumentSafetyReport, InjectionScanner
from app.safety.types import (
    InjectionAssessment,
    InjectionCategory,
    InjectionPolicyConfig,
    InjectionSeverity,
    InjectionSignal,
    SafetyDecision,
)

__all__ = [
    "DocumentSafetyReport",
    "InjectionAssessment",
    "InjectionCategory",
    "InjectionDetector",
    "InjectionPolicyConfig",
    "InjectionScanner",
    "InjectionSeverity",
    "InjectionSignal",
    "SafetyDecision",
    "assess_text",
    "get_default_detector",
]
