"""Malware scanning behind an interface.

The default engine only recognizes the EICAR test signature — enough to make
the quarantine path real and testable. A production engine (e.g. ClamAV)
implements the same protocol and drops in without touching the pipeline.
"""

from dataclasses import dataclass
from typing import Protocol

# The industry-standard antivirus test string (harmless by definition).
EICAR_SIGNATURE = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@dataclass(frozen=True)
class ScanVerdict:
    clean: bool
    reason: str | None = None


class MalwareScanner(Protocol):
    async def scan(self, data: bytes) -> ScanVerdict: ...


class SignatureScanner:
    """Flags only the EICAR test signature; a placeholder, not protection."""

    async def scan(self, data: bytes) -> ScanVerdict:
        if EICAR_SIGNATURE in data:
            return ScanVerdict(clean=False, reason="eicar-test-signature")
        return ScanVerdict(clean=True)
