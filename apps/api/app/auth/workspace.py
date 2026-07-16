"""Workspace authorization context for routes under `/workspaces/{workspace_id}`.

Resolving the context proves membership, loads the workspace, and binds the
transaction to the workspace for row-level security — so any tenant query a
route makes afterwards is doubly fenced. Non-members receive the same 404 as
a nonexistent workspace; workspace existence is never disclosed.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.auth import errors
from app.auth.dependencies import CurrentUserDep, SessionDep
from app.auth.permissions import WorkspaceAction, allows
from app.db.models.identity import Membership, User, Workspace
from app.db.repositories.identity import MembershipRepository, WorkspaceRepository
from app.db.session import bind_workspace


@dataclass(frozen=True)
class WorkspaceContext:
    """An authenticated user's proven standing inside one workspace."""

    workspace: Workspace
    membership: Membership
    user: User


async def get_workspace_context(
    workspace_id: uuid.UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> WorkspaceContext:
    membership = await MembershipRepository(session).get_membership(
        workspace_id,
        current_user.id,
    )
    workspace = await WorkspaceRepository(session).get(workspace_id)
    if membership is None or workspace is None:
        raise errors.workspace_not_found()
    await bind_workspace(session, workspace_id)
    return WorkspaceContext(workspace=workspace, membership=membership, user=current_user)


WorkspaceContextDep = Annotated[WorkspaceContext, Depends(get_workspace_context)]


class RequireAction:
    """Dependency that additionally demands one capability from the role matrix."""

    def __init__(self, action: WorkspaceAction) -> None:
        self._action = action

    async def __call__(self, context: WorkspaceContextDep) -> WorkspaceContext:
        if not allows(context.membership.role, self._action):
            raise errors.insufficient_role()
        return context
