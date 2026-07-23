"""Authentication endpoints under `/api/v1/auth`.

The credential-bearing POST endpoints are rate limited per client and path;
`/me` is protected by the access token itself, so limiting it would only
throttle legitimate polling without slowing an attacker down.
"""

from fastapi import APIRouter, Depends, Request, Response, status

from app.auth import errors
from app.auth.dependencies import (
    AuthServiceDep,
    CurrentUserDep,
    SessionDep,
    enforce_auth_rate_limit,
)
from app.auth.service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.db.repositories.audit import AuditLogRepository
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPairResponse,
    UserResponse,
)
from app.security.events import log_security_event

router = APIRouter(prefix="/auth", tags=["auth"])
_rate_limited = [Depends(enforce_auth_rate_limit)]


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=_rate_limited,
)
async def register(
    body: RegisterRequest, auth: AuthServiceDep, session: SessionDep
) -> UserResponse:
    try:
        user = await auth.register(
            email=body.email,
            password=body.password,
            full_name=body.full_name,
        )
    except EmailAlreadyRegisteredError:
        raise errors.email_already_registered() from None
    await AuditLogRepository(session).record(
        action="auth.user_registered",
        resource_type="user",
        resource_id=user.id,
        actor_user_id=user.id,
    )
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenPairResponse, dependencies=_rate_limited)
async def login(
    body: LoginRequest, auth: AuthServiceDep, session: SessionDep, request: Request
) -> TokenPairResponse:
    try:
        user = await auth.authenticate(email=body.email, password=body.password)
    except InvalidCredentialsError:
        # Failure rolls back the request, so it is logged (never with the
        # submitted email) rather than written to the audit table.
        client = request.client.host if request.client else "unknown"
        log_security_event("login_failed", client=client)
        raise errors.invalid_credentials() from None
    pair = await auth.issue_session(user)
    await AuditLogRepository(session).record(
        action="auth.login_succeeded",
        resource_type="user",
        resource_id=user.id,
        actor_user_id=user.id,
    )
    return TokenPairResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/refresh", response_model=TokenPairResponse, dependencies=_rate_limited)
async def refresh(body: RefreshRequest, auth: AuthServiceDep) -> TokenPairResponse:
    try:
        pair = await auth.rotate_session(body.refresh_token)
    except InvalidRefreshTokenError:
        raise errors.invalid_refresh_token() from None
    return TokenPairResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_rate_limited,
)
async def logout(body: LogoutRequest, auth: AuthServiceDep) -> Response:
    """Revoke one refresh-token session; idempotent by design."""
    await auth.revoke_session(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def me(current_user: CurrentUserDep) -> UserResponse:
    return UserResponse.model_validate(current_user)
