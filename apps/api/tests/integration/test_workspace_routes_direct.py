"""Route-function tests against the real database.

These call the handlers directly (no ASGI layer) with real repositories and
sessions. They pin the handlers' branch behavior — especially the error
paths — at the function level; the HTTP contract itself is covered by
`test_workspaces_api.py`.
"""

import uuid

import pytest
from app.auth.workspace import WorkspaceContext, get_workspace_context
from app.db.models.enums import MembershipRole
from app.db.models.identity import User
from app.db.repositories.identity import MembershipRepository
from app.routes import workspaces as handlers
from app.schemas.workspaces import (
    AddMemberRequest,
    UpdateMemberRoleRequest,
    WorkspaceCreateRequest,
)
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration import factories


async def create(
    session: AsyncSession,
    user: User,
    name: str = "Direct Workspace",
    slug: str | None = None,
) -> uuid.UUID:
    response = await handlers.create_workspace(
        WorkspaceCreateRequest(name=name, slug=slug),
        user,
        session,
    )
    return response.id


async def context_for(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user: User,
) -> WorkspaceContext:
    return await get_workspace_context(workspace_id, user, session)


def error_code(error: HTTPException) -> str:
    detail: object = error.detail
    assert isinstance(detail, dict)
    return str(detail["code"])


async def test_create_list_get_and_members_roundtrip(db_session: AsyncSession) -> None:
    user = await factories.make_user(db_session)
    workspace_id = await create(db_session, user, name="Näme With Ünicode!")

    listed = await handlers.list_my_workspaces(user, db_session)
    assert [item.id for item in listed] == [workspace_id]
    assert listed[0].role is MembershipRole.OWNER

    context = await context_for(db_session, workspace_id, user)
    fetched = await handlers.get_workspace(context)
    assert fetched.id == workspace_id

    members = await handlers.list_members(context, db_session)
    assert [member.user_id for member in members] == [user.id]


async def test_create_with_conflicting_slug_raises(db_session: AsyncSession) -> None:
    user = await factories.make_user(db_session)
    await create(db_session, user, slug="direct-slug")
    with pytest.raises(HTTPException) as conflict:
        await create(db_session, user, slug="direct-slug")
    assert error_code(conflict.value) == "slug_already_exists"


async def test_context_rejects_non_members_and_missing_workspaces(
    db_session: AsyncSession,
) -> None:
    owner = await factories.make_user(db_session)
    outsider = await factories.make_user(db_session)
    workspace_id = await create(db_session, owner)

    with pytest.raises(HTTPException) as not_member:
        await context_for(db_session, workspace_id, outsider)
    assert error_code(not_member.value) == "workspace_not_found"

    with pytest.raises(HTTPException) as missing:
        await context_for(db_session, uuid.uuid4(), owner)
    assert error_code(missing.value) == "workspace_not_found"


async def test_add_member_paths(db_session: AsyncSession) -> None:
    owner = await factories.make_user(db_session)
    joiner = await factories.make_user(db_session)
    workspace_id = await create(db_session, owner)
    context = await context_for(db_session, workspace_id, owner)

    added = await handlers.add_member(
        AddMemberRequest(email=joiner.email, role=MembershipRole.ADMIN),
        context,
        db_session,
    )
    assert added.role is MembershipRole.ADMIN

    with pytest.raises(HTTPException) as duplicate:
        await handlers.add_member(
            AddMemberRequest(email=joiner.email, role=MembershipRole.MEMBER),
            context,
            db_session,
        )
    assert error_code(duplicate.value) == "member_already_exists"

    with pytest.raises(HTTPException) as unknown:
        await handlers.add_member(
            AddMemberRequest(email="nobody@example.com", role=MembershipRole.MEMBER),
            context,
            db_session,
        )
    assert error_code(unknown.value) == "user_not_found"

    admin_context = await context_for(db_session, workspace_id, joiner)
    third = await factories.make_user(db_session)
    with pytest.raises(HTTPException) as escalation:
        await handlers.add_member(
            AddMemberRequest(email=third.email, role=MembershipRole.OWNER),
            admin_context,
            db_session,
        )
    assert error_code(escalation.value) == "cannot_manage_role"


async def test_change_role_paths(db_session: AsyncSession) -> None:
    owner = await factories.make_user(db_session)
    member = await factories.make_user(db_session)
    workspace_id = await create(db_session, owner)
    context = await context_for(db_session, workspace_id, owner)
    await handlers.add_member(
        AddMemberRequest(email=member.email, role=MembershipRole.MEMBER),
        context,
        db_session,
    )

    changed = await handlers.change_member_role(
        member.id,
        UpdateMemberRoleRequest(role=MembershipRole.VIEWER),
        context,
        db_session,
    )
    assert changed.role is MembershipRole.VIEWER

    with pytest.raises(HTTPException) as ghost:
        await handlers.change_member_role(
            uuid.uuid4(),
            UpdateMemberRoleRequest(role=MembershipRole.VIEWER),
            context,
            db_session,
        )
    assert error_code(ghost.value) == "member_not_found"

    with pytest.raises(HTTPException) as sole_owner:
        await handlers.change_member_role(
            owner.id,
            UpdateMemberRoleRequest(role=MembershipRole.MEMBER),
            context,
            db_session,
        )
    assert error_code(sole_owner.value) == "last_owner"

    viewer_context = await context_for(db_session, workspace_id, member)
    with pytest.raises(HTTPException) as forbidden:
        await handlers.change_member_role(
            owner.id,
            UpdateMemberRoleRequest(role=MembershipRole.MEMBER),
            viewer_context,
            db_session,
        )
    assert error_code(forbidden.value) == "cannot_manage_role"


async def test_require_action_enforces_the_role_matrix(db_session: AsyncSession) -> None:
    from app.auth.permissions import WorkspaceAction
    from app.auth.workspace import RequireAction

    owner = await factories.make_user(db_session)
    viewer = await factories.make_user(db_session)
    workspace_id = await create(db_session, owner)
    owner_context = await context_for(db_session, workspace_id, owner)
    await handlers.add_member(
        AddMemberRequest(email=viewer.email, role=MembershipRole.VIEWER),
        owner_context,
        db_session,
    )
    viewer_context = await context_for(db_session, workspace_id, viewer)

    guard = RequireAction(WorkspaceAction.MANAGE_MEMBERS)
    assert await guard(owner_context) is owner_context
    with pytest.raises(HTTPException) as forbidden:
        await guard(viewer_context)
    assert error_code(forbidden.value) == "insufficient_role"


async def test_remove_member_paths(db_session: AsyncSession) -> None:
    owner = await factories.make_user(db_session)
    member = await factories.make_user(db_session)
    workspace_id = await create(db_session, owner)
    context = await context_for(db_session, workspace_id, owner)
    await handlers.add_member(
        AddMemberRequest(email=member.email, role=MembershipRole.MEMBER),
        context,
        db_session,
    )

    with pytest.raises(HTTPException) as ghost:
        await handlers.remove_member(uuid.uuid4(), context, db_session)
    assert error_code(ghost.value) == "member_not_found"

    with pytest.raises(HTTPException) as sole_owner:
        await handlers.remove_member(owner.id, context, db_session)
    assert error_code(sole_owner.value) == "last_owner"

    removed = await handlers.remove_member(member.id, context, db_session)
    assert removed.status_code == 204
    remaining = await MembershipRepository(db_session).list_for_workspace(workspace_id)
    assert [membership.user_id for membership in remaining] == [owner.id]
