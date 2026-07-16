"""Repositories for users, workspaces, and memberships.

These are intentionally unscoped: they operate on identity data that exists
above the tenant boundary (login, workspace creation, role resolution).
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import joinedload

from app.db.models.enums import MembershipRole
from app.db.models.identity import Membership, RefreshToken, User, Workspace
from app.db.repositories.base import Repository


class UserRepository(Repository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        statement = select(User).where(User.email == email)
        result = await self._session.scalars(statement)
        return result.first()


class RefreshTokenRepository(Repository[RefreshToken]):
    model = RefreshToken

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        result = await self._session.scalars(statement)
        return result.first()

    async def revoke(self, token: RefreshToken, *, revoked_at: datetime) -> None:
        token.revoked_at = revoked_at
        await self._session.flush()

    async def revoke_all_for_user(self, user_id: uuid.UUID, *, revoked_at: datetime) -> None:
        statement = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        await self._session.execute(statement)
        await self._session.flush()


class WorkspaceRepository(Repository[Workspace]):
    model = Workspace

    async def get_by_slug(self, slug: str) -> Workspace | None:
        statement = select(Workspace).where(Workspace.slug == slug)
        result = await self._session.scalars(statement)
        return result.first()


class MembershipRepository(Repository[Membership]):
    model = Membership

    async def get_membership(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Membership | None:
        statement = select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
        )
        result = await self._session.scalars(statement)
        return result.first()

    async def list_for_workspace(self, workspace_id: uuid.UUID) -> Sequence[Membership]:
        statement = select(Membership).where(Membership.workspace_id == workspace_id)
        result = await self._session.scalars(statement)
        return result.all()

    async def list_with_users(self, workspace_id: uuid.UUID) -> Sequence[Membership]:
        statement = (
            select(Membership)
            .where(Membership.workspace_id == workspace_id)
            .options(joinedload(Membership.user))
            .order_by(Membership.created_at)
        )
        result = await self._session.scalars(statement)
        return result.all()

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[Membership]:
        statement = (
            select(Membership)
            .where(Membership.user_id == user_id)
            .options(joinedload(Membership.workspace))
            .order_by(Membership.created_at)
        )
        result = await self._session.scalars(statement)
        return result.all()

    async def count_owners(self, workspace_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.role == MembershipRole.OWNER,
            )
        )
        return (await self._session.execute(statement)).scalar_one()
