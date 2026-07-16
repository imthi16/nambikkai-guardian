"""FastAPI dependencies for authentication and auth-endpoint rate limiting."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import errors, tokens
from app.auth.rate_limit import RateLimiter
from app.auth.service import AuthService
from app.config import Settings
from app.db.models.identity import User
from app.db.repositories.identity import UserRepository
from app.db.session import get_db_session

_bearer_scheme = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    """Settings the application was constructed with (not the process cache)."""
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    return AuthService(session, settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)] = None,
) -> User:
    """Resolve the bearer access token to an active user or fail with 401."""
    if credentials is None:
        raise errors.not_authenticated()
    try:
        user_id = tokens.decode_access_token(
            credentials.credentials,
            secret=settings.jwt_secret.get_secret_value(),
        )
    except tokens.InvalidTokenError:
        raise errors.not_authenticated() from None
    user = await UserRepository(session).get(user_id)
    if user is None or not user.is_active:
        raise errors.not_authenticated()
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def enforce_auth_rate_limit(request: Request) -> None:
    """Reject the request with 429 once the caller exceeds the per-route window."""
    limiter: RateLimiter = request.app.state.auth_rate_limiter
    client_host = request.client.host if request.client else "unknown"
    if not limiter.allow(f"{client_host}:{request.url.path}"):
        raise errors.rate_limited()
