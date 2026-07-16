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


def workspace_not_found() -> HTTPException:
    """Also returned to non-members, so workspace existence is never disclosed."""
    return _error(
        status.HTTP_404_NOT_FOUND,
        "workspace_not_found",
        "The workspace does not exist or you are not a member.",
    )


def insufficient_role() -> HTTPException:
    return _error(
        status.HTTP_403_FORBIDDEN,
        "insufficient_role",
        "Your workspace role does not allow this action.",
    )


def cannot_manage_role() -> HTTPException:
    return _error(
        status.HTTP_403_FORBIDDEN,
        "cannot_manage_role",
        "Your workspace role cannot grant or manage the requested role.",
    )


def user_not_found() -> HTTPException:
    return _error(
        status.HTTP_404_NOT_FOUND,
        "user_not_found",
        "No account exists for this email.",
    )


def member_not_found() -> HTTPException:
    return _error(
        status.HTTP_404_NOT_FOUND,
        "member_not_found",
        "This user is not a member of the workspace.",
    )


def member_already_exists() -> HTTPException:
    return _error(
        status.HTTP_409_CONFLICT,
        "member_already_exists",
        "This user is already a member of the workspace.",
    )


def last_owner() -> HTTPException:
    return _error(
        status.HTTP_409_CONFLICT,
        "last_owner",
        "A workspace must keep at least one owner.",
    )


def slug_already_exists() -> HTTPException:
    return _error(
        status.HTTP_409_CONFLICT,
        "slug_already_exists",
        "A workspace with this slug already exists.",
    )
