"""Structured, privacy-safe logging of security-relevant rejections.

Rejections happen on the request-error path, where the per-request database
transaction rolls back — so they cannot be written to the append-only
`audit_logs` table (successful, committed actions are audited there instead).
They are emitted to a dedicated logger so operators can alert on them without
the events ever carrying request bodies, credentials, tokens, or emails.
"""

import logging
from typing import Any

security_logger = logging.getLogger("app.security")


def log_security_event(event: str, **fields: Any) -> None:
    """Emit one security event; `fields` must never contain secrets or PII."""
    security_logger.warning("security.%s", event, extra={"security_event": event, **fields})
