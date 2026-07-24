"""Rule-based prompt-injection detection with a replaceable classifier hook.

The detector runs three passes over untrusted text and merges their signals:

1. **Normalization.** Homoglyph-free, zero-width-stripped, whitespace-collapsed
   views of the text are searched alongside the raw text, so an attacker cannot
   evade a rule with ``i g n o r e`` spacing, soft hyphens, or invisible
   characters. Every match still records offsets into the *original* text.
2. **Rule matching.** Curated regexes for direct instruction overrides, system
   or role impersonation, exfiltration/tool-use requests, and indirect
   ("when you read this…") triggers. Rules are written to fire on *imperative*
   manipulation, not on benign prose that merely mentions instructions, so that
   ordinary policy language ("this policy supersedes all previous versions")
   does not trip the detector.
3. **Structural heuristics.** Obfuscation (invisible characters, letter
   spacing) and encoded payloads (base64/hex blocks that decode to
   instruction-like text) are detected structurally, since their whole purpose
   is to hide from the regex pass.

An optional :class:`InjectionClassifier` can add its own signal; the default
build ships without one so the pipeline is deterministic and dependency-free.
The detector only *measures* content — it never decodes-and-obeys or executes
anything it finds.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.safety.types import (
    InjectionAssessment,
    InjectionCategory,
    InjectionPolicyConfig,
    InjectionSeverity,
    InjectionSignal,
    SafetyDecision,
)

# --- normalization ---------------------------------------------------------

# Zero-width and invisible formatting characters an attacker inserts to break a
# literal match while leaving the rendered text unchanged.
_INVISIBLE = {
    "\u200b",  # zero width space
    "\u200c",  # zero width non-joiner
    "\u200d",  # zero width joiner
    "\u2060",  # word joiner
    "\ufeff",  # zero width no-break space / BOM
    "\u00ad",  # soft hyphen
    "\u180e",  # mongolian vowel separator
}
# Common homoglyph substitutions (Cyrillic/Greek look-alikes) folded to ASCII.
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "ѕ": "s",
        "і": "i",
        "ј": "j",
        "ԁ": "d",
        "ɡ": "g",
        "ⅼ": "l",
        "ν": "v",
        "ο": "o",
        "Ι": "I",
        "Ѕ": "S",
        "Α": "A",
        "Е": "E",
        "О": "O",
        "Р": "P",
        "С": "C",
    }
)
_EXCERPT_RADIUS = 40
_MAX_EXCERPT = 120


def _strip_invisible(text: str) -> str:
    return "".join(ch for ch in text if ch not in _INVISIBLE)


def _fold(text: str) -> str:
    """A comparison view: NFKC, homoglyph-folded, invisible-stripped, lowercased."""
    folded = unicodedata.normalize("NFKC", text)
    folded = _strip_invisible(folded).translate(_HOMOGLYPHS)
    return folded.lower()


_SPACED_RUN = re.compile(r"(?:\w[ \t\u00a0\-_.]){3,}\w")


def _despace(text: str) -> str:
    """Collapse letter-spaced runs ("i g n o r e" -> "ignore") in place.

    Only runs of single characters separated by one spacer are collapsed, so
    ordinary words and spaced initials are left intact. Length is preserved
    approximately enough for rule matching; offsets from this view are treated
    as approximate and clamped by the caller.
    """

    def _collapse(match: re.Match[str]) -> str:
        return re.sub(r"[ \t\u00a0\-_.]", "", match.group(0))

    return _SPACED_RUN.sub(_collapse, text)


def _redact_excerpt(text: str, start: int, end: int) -> str:
    """A short, single-line, length-bounded window around a match, safe to log."""
    left = max(0, start - _EXCERPT_RADIUS)
    right = min(len(text), end + _EXCERPT_RADIUS)
    window = text[left:right]
    window = _strip_invisible(window)
    window = re.sub(r"\s+", " ", window).strip()
    if len(window) > _MAX_EXCERPT:
        window = window[:_MAX_EXCERPT].rstrip() + "…"
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(text) else ""
    return f"{prefix}{window}{suffix}"


# --- rules -----------------------------------------------------------------


class _Rule:
    """One compiled detection rule and the signal it produces on a match."""

    __slots__ = ("name", "category", "severity", "pattern")

    def __init__(
        self,
        name: str,
        category: InjectionCategory,
        severity: InjectionSeverity,
        pattern: str,
    ) -> None:
        self.name = name
        self.category = category
        self.severity = severity
        self.pattern = re.compile(pattern, re.IGNORECASE | re.UNICODE)


# Rules match imperative manipulation, not incidental mentions. Anchors like a
# leading verb, "you"/"assistant"/"AI" targeting, and colons after role names
# keep benign prose (which describes rather than commands) below threshold.
_RULES: tuple[_Rule, ...] = (
    _Rule(
        "ignore_previous",
        InjectionCategory.INSTRUCTION_OVERRIDE,
        InjectionSeverity.HIGH,
        r"\b(?:ignore|disregard|forget|discard|override)\b[^.\n]{0,40}"
        r"\b(?:previous|prior|earlier|above|preceding|all|your|the|these|those|any)\b"
        r"[^.\n]{0,30}"
        r"\b(?:instruction|instructions|prompt|prompts|direction|directions|rule|rules|"
        r"context|message|messages|command|commands|guideline|guidelines|question|query)\b",
    ),
    _Rule(
        # Reversed word order ("instructions … ignore"), which Tanglish and some
        # translated payloads use and the forward rule above would miss.
        "instructions_ignore",
        InjectionCategory.INSTRUCTION_OVERRIDE,
        InjectionSeverity.HIGH,
        r"\b(?:instruction|instructions|prompt|prompts|rule|rules|guideline|guidelines)\b"
        r"[\w\s\-]{0,25}\b(?:ignore|disregard|forget|override|purakkani\w*)\b",
    ),
    _Rule(
        # Tamil combining marks make Latin-style word boundaries unreliable.
        # Match stable lexical stems so common case and imperative suffixes are
        # covered without treating a lone mention of instructions as malicious.
        "tamil_ignore_previous",
        InjectionCategory.INSTRUCTION_OVERRIDE,
        InjectionSeverity.HIGH,
        r"(?:முந்தைய|முன்னைய|மேலுள்ள|மேற்கண்ட|அனைத்து)[^.\n]{0,60}"
        r"அறிவுறுத்தல்[^.\n]{0,35}புறக்கணி",
    ),
    _Rule(
        # A bare "system prompt" reference is rare in genuine document prose but
        # ubiquitous in exfiltration attempts; medium on its own, decisive when
        # it co-occurs with an override or reveal signal.
        "system_prompt_mention",
        InjectionCategory.EXFILTRATION,
        InjectionSeverity.MEDIUM,
        r"\b(?:system|developer)\s+prompt\b",
    ),
    _Rule(
        # The hallmark indirect (data-borne) injection: an instruction that
        # fires "when you read/process this". Unambiguous, so high severity.
        "indirect_read_override",
        InjectionCategory.INDIRECT_TRIGGER,
        InjectionSeverity.HIGH,
        r"\b(?:when|whenever|after|once|as soon as)\b[^.\n]{0,25}"
        r"\byou\b[^.\n]{0,15}\b(?:read|process|see|analyz\w+|summariz\w+|encounter|receiv\w+)\b"
        r"[^.\n]{0,40}\b(?:ignore|disregard|instead|always|do not|don't|reply|respond|"
        r"answer|say|output|forget)\b",
    ),
    _Rule(
        "new_instructions",
        InjectionCategory.INSTRUCTION_OVERRIDE,
        InjectionSeverity.MEDIUM,
        r"\b(?:here are|these are|follow)\b[^.\n]{0,20}\b(?:new|updated|real|actual)\b"
        r"[^.\n]{0,20}\b(?:instruction|instructions|rule|rules|prompt)\b",
    ),
    _Rule(
        "do_not_follow",
        InjectionCategory.INSTRUCTION_OVERRIDE,
        InjectionSeverity.MEDIUM,
        r"\b(?:do not|don't|no longer)\b[^.\n]{0,20}\b(?:follow|obey|adhere to|comply)\b"
        r"[^.\n]{0,30}\b(?:instruction|instructions|rule|rules|prompt|guideline|guidelines)\b",
    ),
    _Rule(
        "system_prompt_reveal",
        InjectionCategory.EXFILTRATION,
        InjectionSeverity.HIGH,
        r"\b(?:reveal|show|print|repeat|output|display|expose|tell me|give me)\b[^.\n]{0,30}"
        r"\b(?:system|initial|original|hidden|secret|developer)\b[^.\n]{0,10}"
        r"\b(?:prompt|instruction|instructions|message|rules|configuration)\b",
    ),
    _Rule(
        "tamil_system_prompt_reveal",
        InjectionCategory.EXFILTRATION,
        InjectionSeverity.HIGH,
        r"(?:கணினி|அமைப்பு|மறைக்கப்பட்ட|ரகசிய)[^.\n]{0,30}"
        r"(?:தூண்டுத|அறிவுறுத்தல)[^.\n]{0,35}"
        r"(?:வெளிப்படுத்து|காட்டு|அச்சிடு|தெரிவி)",
    ),
    _Rule(
        "exfiltrate_secrets",
        InjectionCategory.EXFILTRATION,
        InjectionSeverity.HIGH,
        r"\b(?:reveal|show|print|send|leak|exfiltrate|forward|email|post|upload|disclose)\b"
        r"[^.\n]{0,30}\b(?:api[ _-]?key|secret|password|credential|token|private key|"
        r"env(?:ironment)? variable)\b",
    ),
    _Rule(
        "role_impersonation",
        InjectionCategory.ROLE_IMPERSONATION,
        InjectionSeverity.HIGH,
        r"(?:^|[\n>\-\*\s])(?:system|assistant|developer)\s*(?:message|prompt)?\s*:\s*"
        r"(?:"
        r"(?:please\s+)?(?:ignore|disregard|forget|override|reveal|show|print|output|"
        r"leak|exfiltrate|bypass|disable|disclose|expose)\b[^.\n]{0,35}"
        r"\b(?:instruction|instructions|prompt|prompts|rule|rules|guideline|guidelines|"
        r"guardrail|guardrails|safety|filter|filters|secret|password|credential|token)\b"
        r"|(?:send|email|forward|post|upload)\b[^.\n]{0,30}"
        r"\b(?:api[ _-]?key|secret|password|credential|token|private key)\b"
        r"|[^.\n]{0,25}\b(?:assistant|ai|model|you|i)\b[^.\n]{0,25}"
        r"\b(?:must|should|will|shall|need to|have to|am going to)\b[^.\n]{0,30}"
        r"\b(?:ignore|disregard|forget|override|reveal|show|print|output|send|email|"
        r"forward|post|upload|leak|exfiltrate|bypass|disable|disclose|expose)\b"
        r"[^.\n]{0,35}\b(?:instruction|instructions|prompt|prompts|rule|rules|guideline|"
        r"guidelines|guardrail|guardrails|safety|filter|filters|api[ _-]?key|secret|"
        r"password|credential|token|private key)\b"
        r")",
    ),
    _Rule(
        "you_are_now",
        InjectionCategory.ROLE_IMPERSONATION,
        InjectionSeverity.HIGH,
        r"\byou (?:are|act|will act|must act|shall act) (?:now |from now on )?"
        r"(?:an? |the )?(?:dan\b|do anything now|jailbroken|unrestricted|"
        r"developer mode|no(?:t| longer) bound|unfiltered)",
    ),
    _Rule(
        "pretend_bypass",
        InjectionCategory.ROLE_IMPERSONATION,
        InjectionSeverity.MEDIUM,
        r"\b(?:pretend|imagine|act as if|roleplay)\b[^.\n]{0,30}"
        r"\b(?:no (?:rules|restrictions|guidelines|limits)|without (?:rules|restrictions|filter)|"
        r"you can do anything|unrestricted)\b",
    ),
    _Rule(
        "override_safety",
        InjectionCategory.INSTRUCTION_OVERRIDE,
        InjectionSeverity.HIGH,
        r"\b(?:bypass|disable|turn off|switch off|ignore|override|circumvent)\b[^.\n]{0,25}"
        r"\b(?:safety|guardrail|guardrails|filter|filters|moderation|content policy|"
        r"restriction|restrictions|safeguard|safeguards)\b",
    ),
    _Rule(
        "indirect_trigger",
        InjectionCategory.INDIRECT_TRIGGER,
        InjectionSeverity.MEDIUM,
        r"\b(?:when|whenever|as soon as|after|once)\b[^.\n]{0,20}"
        r"\b(?:you (?:read|process|see|analyze|summariz\w+|encounter)|reading|processing)\b"
        r"[^.\n]{0,30}\b(?:you (?:must|should|will|have to|need to)|instead|always|do not|"
        r"ignore|reply|respond|say|output)\b",
    ),
    _Rule(
        "tool_use_request",
        InjectionCategory.EXFILTRATION,
        InjectionSeverity.MEDIUM,
        r"\b(?:call|invoke|execute|run|use)\b[^.\n]{0,15}"
        r"\b(?:tool|function|command|shell|api|endpoint|url)\b[^.\n]{0,25}"
        r"\b(?:http|https|curl|wget|fetch|post|get)\b",
    ),
)

# Instruction-like vocabulary used to judge whether a *decoded* payload is
# itself an attempt to inject, so a benign base64 asset never quarantines.
_DECODED_INJECTION = re.compile(
    r"\b(?:ignore|disregard|override|system prompt|you are now|reveal|api[ _-]?key|"
    r"password|secret|bypass|jailbreak|do anything now)\b",
    re.IGNORECASE,
)
_BASE64_BLOCK = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")
_HEX_BLOCK = re.compile(r"(?:[0-9a-fA-F]{2}[\s:]?){16,}")
_LETTER_SPACING = re.compile(r"(?:\b\w[ \t\u00a0\-_.]){5,}\w\b")


@runtime_checkable
class InjectionClassifier(Protocol):
    """A replaceable secondary detector (e.g. a hosted or local ML classifier).

    Implementations return an extra probability in [0, 1] plus any signals they
    can attribute. They must be deterministic for a given input and must never
    follow or execute the content — only score it.
    """

    name: str
    version: str

    def classify(self, text: str) -> tuple[float, Sequence[InjectionSignal]]: ...


class InjectionDetector:
    """Assesses untrusted text for prompt-injection, deterministically.

    The aggregate score combines the strongest signal with a saturating bonus
    from additional distinct signals, so several weak indicators can raise
    suspicion without any single benign phrase dominating, while a lone
    high-severity match is decisive on its own. A classifier, when supplied,
    contributes its probability as one more bounded term.
    """

    def __init__(
        self,
        *,
        policy: InjectionPolicyConfig | None = None,
        classifier: InjectionClassifier | None = None,
    ) -> None:
        self._policy = policy or InjectionPolicyConfig()
        self._classifier = classifier
        self.name = "rule-based" if classifier is None else f"rule+{classifier.name}"
        self.version = "v1" if classifier is None else f"v1+{classifier.version}"

    def assess(self, text: str) -> InjectionAssessment:
        """Return the full assessment (score, decision, signals) for one text."""
        if not text or not text.strip():
            return InjectionAssessment(
                score=0.0,
                decision=SafetyDecision.ALLOW,
                detector=self.name,
                detector_version=self.version,
            )

        signals = list(self._rule_signals(text))
        signals.extend(self._structural_signals(text))

        classifier_score = 0.0
        if self._classifier is not None:
            classifier_score, extra = self._classifier.classify(text)
            classifier_score = _clamp(classifier_score)
            signals.extend(extra)

        signals = _dedupe(signals)
        score = self._aggregate(signals, classifier_score)
        has_high = any(s.severity is InjectionSeverity.HIGH for s in signals)
        decision = self._policy.decide(score, has_high_severity=has_high)
        return InjectionAssessment(
            score=score,
            decision=decision,
            signals=tuple(signals),
            detector=self.name,
            detector_version=self.version,
        )

    # --- passes ------------------------------------------------------------

    def _rule_signals(self, text: str) -> list[InjectionSignal]:
        """Run every rule over the raw and folded views, mapping offsets back."""
        signals: list[InjectionSignal] = []
        folded = _fold(text)
        # A de-spaced view collapses letter-spacing ("i g n o r e") so the rules
        # match the underlying instruction; offsets there are approximate and
        # clamped back into the original text for the excerpt window.
        despaced = _despace(folded)
        folded_differs = folded != text.lower()
        for rule in _RULES:
            for match in rule.pattern.finditer(text):
                signals.append(self._signal(rule, text, match.start(), match.end()))
            if folded_differs:
                for match in rule.pattern.finditer(folded):
                    start = min(match.start(), len(text))
                    end = min(match.end(), len(text))
                    signals.append(self._signal(rule, text, start, end, obfuscated=True))
            if despaced != folded:
                # A match anywhere in the de-spaced view means the underlying
                # instruction is present; offsets there do not map to the
                # original, so the signal points at the start as a marker.
                if rule.pattern.search(despaced):
                    signals.append(self._signal(rule, text, 0, min(len(text), 1), obfuscated=True))
        return signals

    def _structural_signals(self, text: str) -> list[InjectionSignal]:
        signals: list[InjectionSignal] = []
        signals.extend(self._obfuscation_signals(text))
        signals.extend(self._encoded_signals(text))
        return signals

    def _obfuscation_signals(self, text: str) -> list[InjectionSignal]:
        signals: list[InjectionSignal] = []
        invisible_count = sum(1 for ch in text if ch in _INVISIBLE)
        # A couple of soft hyphens can appear in legitimately typeset text; only
        # an unusual density signals deliberate hiding.
        if invisible_count >= 4:
            signals.append(
                InjectionSignal(
                    category=InjectionCategory.OBFUSCATION,
                    severity=InjectionSeverity.MEDIUM,
                    rule="invisible_characters",
                    start=0,
                    end=min(len(text), 1),
                    excerpt=f"{invisible_count} invisible characters",
                )
            )
        for match in _LETTER_SPACING.finditer(text):
            # Letter-spacing is only suspicious when the collapsed run spells an
            # instruction-like word, so ordinary spaced initials do not trip it.
            collapsed = re.sub(r"[ \t\u00a0\-_.]", "", match.group(0)).lower()
            if _DECODED_INJECTION.search(collapsed) or collapsed in _SPACED_KEYWORDS:
                signals.append(
                    self._raw_signal(
                        InjectionCategory.OBFUSCATION,
                        InjectionSeverity.MEDIUM,
                        "letter_spacing",
                        text,
                        match.start(),
                        match.end(),
                    )
                )
        return signals

    def _encoded_signals(self, text: str) -> list[InjectionSignal]:
        signals: list[InjectionSignal] = []
        for match in _BASE64_BLOCK.finditer(text):
            decoded = _try_base64(match.group(0))
            if decoded is not None and _DECODED_INJECTION.search(decoded):
                signals.append(
                    self._raw_signal(
                        InjectionCategory.ENCODED_PAYLOAD,
                        InjectionSeverity.HIGH,
                        "base64_instruction",
                        text,
                        match.start(),
                        match.end(),
                    )
                )
        for match in _HEX_BLOCK.finditer(text):
            decoded = _try_hex(match.group(0))
            if decoded is not None and _DECODED_INJECTION.search(decoded):
                signals.append(
                    self._raw_signal(
                        InjectionCategory.ENCODED_PAYLOAD,
                        InjectionSeverity.HIGH,
                        "hex_instruction",
                        text,
                        match.start(),
                        match.end(),
                    )
                )
        return signals

    # --- helpers -----------------------------------------------------------

    def _signal(
        self,
        rule: _Rule,
        text: str,
        start: int,
        end: int,
        *,
        obfuscated: bool = False,
    ) -> InjectionSignal:
        name = f"{rule.name}:obfuscated" if obfuscated else rule.name
        return InjectionSignal(
            category=rule.category,
            severity=rule.severity,
            rule=name,
            start=start,
            end=end,
            excerpt=_redact_excerpt(text, start, end),
        )

    def _raw_signal(
        self,
        category: InjectionCategory,
        severity: InjectionSeverity,
        rule: str,
        text: str,
        start: int,
        end: int,
    ) -> InjectionSignal:
        return InjectionSignal(
            category=category,
            severity=severity,
            rule=rule,
            start=start,
            end=end,
            excerpt=_redact_excerpt(text, start, end),
        )

    def _aggregate(self, signals: Sequence[InjectionSignal], classifier_score: float) -> float:
        """Combine signals into a bounded [0, 1] score.

        The strongest signal sets the floor; every additional *distinct* signal
        adds a saturating increment so corroborating indicators raise the score
        without unbounded growth. The classifier probability is blended as an
        independent term via the same saturating combiner.
        """
        weights = sorted((s.severity.weight for s in signals), reverse=True)
        score = 0.0
        for index, weight in enumerate(weights):
            # First signal at full weight; later ones with diminishing returns.
            score = _combine(score, weight * (1.0 if index == 0 else 0.5))
        if classifier_score > 0.0:
            score = _combine(score, classifier_score)
        return round(_clamp(score), 6)


_SPACED_KEYWORDS = {"ignore", "system", "reveal", "bypass", "override", "jailbreak"}


def _combine(current: float, addition: float) -> float:
    """Probabilistic OR: two independent indicators never exceed 1.0."""
    return current + addition - current * addition


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _dedupe(signals: Sequence[InjectionSignal]) -> list[InjectionSignal]:
    """Drop duplicate (rule, span) signals, keeping the first occurrence."""
    seen: set[tuple[str, int, int]] = set()
    unique: list[InjectionSignal] = []
    for signal in signals:
        key = (signal.rule, signal.start, signal.end)
        if key in seen:
            continue
        seen.add(key)
        unique.append(signal)
    return unique


def _try_base64(blob: str) -> str | None:
    # RFC 4648 URL-safe payloads replace '+'/'/' with '-'/'_', and many
    # transports omit trailing padding. Normalize both variants before strict
    # decoding so representation changes cannot bypass inspection.
    padded = blob + "=" * (-len(blob) % 4)
    try:
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # Require mostly-printable output so random binary never looks "decoded".
    printable = sum(1 for ch in decoded if ch.isprintable() or ch.isspace())
    if not decoded or printable / len(decoded) < 0.8:
        return None
    return decoded


def _try_hex(blob: str) -> str | None:
    cleaned = re.sub(r"[\s:]", "", blob)
    if len(cleaned) % 2 != 0:
        cleaned = cleaned[:-1]
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError:
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = sum(1 for ch in decoded if ch.isprintable() or ch.isspace())
    if not decoded or printable / len(decoded) < 0.8:
        return None
    return decoded


_DEFAULT_DETECTOR = InjectionDetector()


def get_default_detector() -> InjectionDetector:
    """The rule-based detector wired for the MVP (no external classifier)."""
    return _DEFAULT_DETECTOR


def assess_text(text: str) -> InjectionAssessment:
    """Assess one untrusted text with the default detector."""
    return _DEFAULT_DETECTOR.assess(text)
