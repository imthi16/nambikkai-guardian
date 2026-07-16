"""Access-token (JWT) and refresh-token primitives.

Access tokens are short-lived HS256 JWTs carrying only the user id. Refresh
tokens are opaque random secrets; the database stores their SHA-256 digest so
a leaked table does not yield usable credentials.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt

_ALGORITHM = "HS256"
_TOKEN_TYPE_CLAIM = "access"  # noqa: S105 - claim label, not a credential


class InvalidTokenError(Exception):
    """The presented token is malformed, expired, or not an access token."""


def utcnow() -> datetime:
    """Single time source for token lifetimes; patchable in tests."""
    return datetime.now(UTC)


def issue_access_token(
    user_id: uuid.UUID,
    *,
    secret: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> str:
    """Sign a short-lived access token for one user."""
    issued_at = now or utcnow()
    claims = {
        "sub": str(user_id),
        "typ": _TOKEN_TYPE_CLAIM,
        "iat": issued_at,
        "exp": issued_at + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(claims, secret, algorithm=_ALGORITHM)


def decode_access_token(token: str, *, secret: str) -> uuid.UUID:
    """Validate an access token and return the user id it was issued to."""
    try:
        claims = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as error:
        raise InvalidTokenError from error
    if claims.get("typ") != _TOKEN_TYPE_CLAIM:
        raise InvalidTokenError
    try:
        return uuid.UUID(str(claims.get("sub")))
    except ValueError as error:
        raise InvalidTokenError from error


def generate_refresh_token() -> str:
    """Return a new opaque refresh-token secret with ~256 bits of entropy."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """Digest a refresh token for storage and lookup; the raw value is never stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
