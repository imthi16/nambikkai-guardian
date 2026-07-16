"""Stable, machine-readable HTTP errors for authentication.

Every auth failure surfaces as `{"detail": {"code": ..., "message": ...}}`
with a code that clients may rely on; messages are human wording and may
change. Credential and token failures deliberately share one code each so
responses cannot be used to probe which accounts or sessions exist.
"""

from fastapi import HTTPException, status

_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def _error(status_code: int, code: str, message: str, *, challenge: bool = False) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=_BEARER_CHALLENGE if challenge else None,
    )


def email_already_registered() -> HTTPException:
    return _error(
        status.HTTP_409_CONFLICT,
        "email_already_registered",
        "An account with this email already exists.",
    )


def invalid_credentials() -> HTTPException:
    return _error(
        status.HTTP_401_UNAUTHORIZED,
        "invalid_credentials",
        "The email or password is incorrect.",
        challenge=True,
    )


def invalid_refresh_token() -> HTTPException:
    return _error(
        status.HTTP_401_UNAUTHORIZED,
        "invalid_refresh_token",
        "The refresh token is invalid or expired.",
        challenge=True,
    )


def not_authenticated() -> HTTPException:
    return _error(
        status.HTTP_401_UNAUTHORIZED,
        "not_authenticated",
        "A valid bearer access token is required.",
        challenge=True,
    )


def rate_limited() -> HTTPException:
    return _error(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "rate_limited",
        "Too many attempts; retry later.",
    )
