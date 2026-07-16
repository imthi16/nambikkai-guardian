"""Authentication use-cases on top of the identity repositories.

All failure modes collapse into three stable errors so responses cannot be
used to enumerate accounts: registration conflicts, bad login credentials
(unknown email, wrong password, and deactivated account are indistinguishable),
and unusable refresh tokens.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import passwords, tokens
from app.config import Settings
from app.db.models.identity import RefreshToken, User
from app.db.repositories.identity import RefreshTokenRepository, UserRepository


class EmailAlreadyRegisteredError(Exception):
    """The email is already attached to an account."""


class InvalidCredentialsError(Exception):
    """The email/password pair does not identify an active account."""


class InvalidRefreshTokenError(Exception):
    """The refresh token is unknown, expired, revoked, or reused."""


@dataclass(frozen=True)
class TokenPair:
    """One issued session: a bearer access token and its rotation credential."""

    access_token: str
    refresh_token: str
    expires_in: int


class AuthService:
    """Registration, login, refresh rotation, and revocation."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._users = UserRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)
        self._settings = settings

    async def register(self, *, email: str, password: str, full_name: str) -> User:
        normalized_email = email.strip().lower()
        if await self._users.get_by_email(normalized_email) is not None:
            raise EmailAlreadyRegisteredError
        user = User(
            email=normalized_email,
            password_hash=passwords.hash_password(password),
            full_name=full_name,
        )
        return await self._users.add(user)

    async def authenticate(self, *, email: str, password: str) -> User:
        user = await self._users.get_by_email(email.strip().lower())
        if user is None:
            passwords.burn_verification_time(password)
            raise InvalidCredentialsError
        if not passwords.verify_password(user.password_hash, password):
            raise InvalidCredentialsError
        if not user.is_active:
            raise InvalidCredentialsError
        return user

    async def issue_session(self, user: User) -> TokenPair:
        refresh_token = tokens.generate_refresh_token()
        await self._refresh_tokens.add(
            RefreshToken(
                user_id=user.id,
                token_hash=tokens.hash_refresh_token(refresh_token),
                expires_at=tokens.utcnow()
                + timedelta(seconds=self._settings.refresh_token_ttl_seconds),
            )
        )
        access_token = tokens.issue_access_token(
            user.id,
            secret=self._settings.jwt_secret.get_secret_value(),
            ttl_seconds=self._settings.access_token_ttl_seconds,
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._settings.access_token_ttl_seconds,
        )

    async def rotate_session(self, refresh_token: str) -> TokenPair:
        now = tokens.utcnow()
        stored = await self._refresh_tokens.get_by_hash(tokens.hash_refresh_token(refresh_token))
        if stored is None:
            raise InvalidRefreshTokenError
        if stored.revoked_at is not None:
            # A revoked token coming back means it was captured: end every
            # session for the account rather than only rejecting this call.
            await self._refresh_tokens.revoke_all_for_user(stored.user_id, revoked_at=now)
            raise InvalidRefreshTokenError
        if stored.expires_at <= now:
            await self._refresh_tokens.revoke(stored, revoked_at=now)
            raise InvalidRefreshTokenError
        user = await self._users.get(stored.user_id)
        if user is None or not user.is_active:
            await self._refresh_tokens.revoke(stored, revoked_at=now)
            raise InvalidRefreshTokenError
        await self._refresh_tokens.revoke(stored, revoked_at=now)
        return await self.issue_session(user)

    async def revoke_session(self, refresh_token: str) -> None:
        stored = await self._refresh_tokens.get_by_hash(tokens.hash_refresh_token(refresh_token))
        if stored is not None and stored.revoked_at is None:
            await self._refresh_tokens.revoke(stored, revoked_at=tokens.utcnow())
