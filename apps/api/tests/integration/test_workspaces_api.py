"""Workspace and membership authorization flows, including the role matrix."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from app.db.models.operations import AuditLog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.apptools import Account, build_client, build_settings, make_account


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    async with build_client(db_session, build_settings()) as instance:
        yield instance


async def create_workspace(
    client: httpx.AsyncClient,
    account: Account,
    name: str = "Evidence Locker",
    slug: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, str] = {"name": name}
    if slug is not None:
        payload["slug"] = slug
    response = await client.post("/api/v1/workspaces", json=payload, headers=account.headers)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def add_member(
    client: httpx.AsyncClient,
    actor: Account,
    workspace_id: str,
    email: str,
    role: str,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"email": email, "role": role},
        headers=actor.headers,
    )


async def workspace_with_roles(
    client: httpx.AsyncClient,
    owner: Account,
    members: dict[str, tuple[Account, str]],
) -> str:
    """Create a workspace and enroll each account at the requested role."""
    workspace = await create_workspace(client, owner)
    workspace_id: str = workspace["id"]
    for account, role in members.values():
        response = await add_member(client, owner, workspace_id, account.email, role)
        assert response.status_code == 201, response.text
    return workspace_id


async def test_create_workspace_makes_creator_owner_and_audits(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await make_account(client, "owner@example.com")
    workspace = await create_workspace(client, owner, name="My Tamil Docs!")
    assert workspace["role"] == "owner"
    assert workspace["slug"].startswith("my-tamil-docs-")

    listed = await client.get("/api/v1/workspaces", headers=owner.headers)
    assert [w["id"] for w in listed.json()] == [workspace["id"]]
    assert listed.json()[0]["role"] == "owner"

    audit = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "workspace.created"))
    ).all()
    assert len(audit) == 1
    assert str(audit[0].workspace_id) == workspace["id"]
    assert str(audit[0].actor_user_id) == owner.user_id


async def test_explicit_slug_conflict_is_rejected(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "owner@example.com")
    await create_workspace(client, owner, slug="shared-slug")
    response = await client.post(
        "/api/v1/workspaces",
        json={"name": "Another", "slug": "shared-slug"},
        headers=owner.headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "slug_already_exists"


async def test_workspace_is_invisible_to_non_members(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "owner@example.com")
    outsider = await make_account(client, "outsider@example.com")
    workspace = await create_workspace(client, owner)

    for path in (
        f"/api/v1/workspaces/{workspace['id']}",
        f"/api/v1/workspaces/{workspace['id']}/members",
    ):
        response = await client.get(path, headers=outsider.headers)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "workspace_not_found"

    missing = await client.get(f"/api/v1/workspaces/{uuid.uuid4()}", headers=owner.headers)
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "workspace_not_found"


async def test_workspace_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/workspaces")
    assert response.status_code == 401


async def test_member_and_viewer_cannot_manage_members(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "owner@example.com")
    member = await make_account(client, "member@example.com")
    viewer = await make_account(client, "viewer@example.com")
    candidate = await make_account(client, "candidate@example.com")
    workspace_id = await workspace_with_roles(
        client,
        owner,
        {"member": (member, "member"), "viewer": (viewer, "viewer")},
    )

    for actor in (member, viewer):
        response = await add_member(client, actor, workspace_id, candidate.email, "member")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "insufficient_role"


async def test_admin_can_manage_only_unprivileged_roles(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "owner@example.com")
    admin = await make_account(client, "admin@example.com")
    candidate = await make_account(client, "candidate@example.com")
    workspace_id = await workspace_with_roles(client, owner, {"admin": (admin, "admin")})

    allowed = await add_member(client, admin, workspace_id, candidate.email, "viewer")
    assert allowed.status_code == 201
    assert allowed.json()["role"] == "viewer"

    second = await make_account(client, "second@example.com")
    for privileged_role in ("admin", "owner"):
        refused = await add_member(client, admin, workspace_id, second.email, privileged_role)
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "cannot_manage_role"

    promoted_by_owner = await add_member(client, owner, workspace_id, second.email, "admin")
    assert promoted_by_owner.status_code == 201


async def test_add_member_edge_cases(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "owner@example.com")
    member = await make_account(client, "member@example.com")
    workspace_id = await workspace_with_roles(client, owner, {"member": (member, "member")})

    unknown = await add_member(client, owner, workspace_id, "ghost@example.com", "member")
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "user_not_found"

    duplicate = await add_member(client, owner, workspace_id, member.email, "viewer")
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "member_already_exists"


async def test_role_changes_follow_management_rules(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await make_account(client, "owner@example.com")
    admin = await make_account(client, "admin@example.com")
    member = await make_account(client, "member@example.com")
    workspace_id = await workspace_with_roles(
        client,
        owner,
        {"admin": (admin, "admin"), "member": (member, "member")},
    )

    demoted = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{member.user_id}",
        json={"role": "viewer"},
        headers=admin.headers,
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "viewer"

    touching_admin = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{admin.user_id}",
        json={"role": "member"},
        headers=admin.headers,
    )
    assert touching_admin.status_code == 403

    promoted = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{member.user_id}",
        json={"role": "admin"},
        headers=owner.headers,
    )
    assert promoted.status_code == 200

    audit = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "member.role_changed"))
    ).all()
    assert {entry.detail["to_role"] for entry in audit} == {"viewer", "admin"}

    ghost = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{uuid.uuid4()}",
        json={"role": "viewer"},
        headers=owner.headers,
    )
    assert ghost.status_code == 404
    assert ghost.json()["detail"]["code"] == "member_not_found"


async def test_the_last_owner_is_protected(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "owner@example.com")
    partner = await make_account(client, "partner@example.com")
    workspace_id = await workspace_with_roles(client, owner, {"partner": (partner, "member")})

    demote_self = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{owner.user_id}",
        json={"role": "member"},
        headers=owner.headers,
    )
    assert demote_self.status_code == 409
    assert demote_self.json()["detail"]["code"] == "last_owner"

    remove_self = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/members/{owner.user_id}",
        headers=owner.headers,
    )
    assert remove_self.status_code == 409

    promote_partner = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{partner.user_id}",
        json={"role": "owner"},
        headers=owner.headers,
    )
    assert promote_partner.status_code == 200

    demote_self_now = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/members/{owner.user_id}",
        json={"role": "member"},
        headers=owner.headers,
    )
    assert demote_self_now.status_code == 200


async def test_member_removal_rules_and_access_loss(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "owner@example.com")
    admin = await make_account(client, "admin@example.com")
    other_admin = await make_account(client, "other-admin@example.com")
    member = await make_account(client, "member@example.com")
    workspace_id = await workspace_with_roles(
        client,
        owner,
        {
            "admin": (admin, "admin"),
            "other_admin": (other_admin, "admin"),
            "member": (member, "member"),
        },
    )

    admin_removes_admin = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/members/{other_admin.user_id}",
        headers=admin.headers,
    )
    assert admin_removes_admin.status_code == 403

    admin_removes_member = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/members/{member.user_id}",
        headers=admin.headers,
    )
    assert admin_removes_member.status_code == 204

    lost_access = await client.get(
        f"/api/v1/workspaces/{workspace_id}",
        headers=member.headers,
    )
    assert lost_access.status_code == 404

    owner_removes_admin = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/members/{other_admin.user_id}",
        headers=owner.headers,
    )
    assert owner_removes_admin.status_code == 204


async def test_member_listing_is_visible_to_viewers(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "owner@example.com")
    viewer = await make_account(client, "viewer@example.com")
    workspace_id = await workspace_with_roles(client, owner, {"viewer": (viewer, "viewer")})

    response = await client.get(
        f"/api/v1/workspaces/{workspace_id}/members",
        headers=viewer.headers,
    )
    assert response.status_code == 200
    listed = {entry["email"]: entry["role"] for entry in response.json()}
    assert listed == {owner.email: "owner", viewer.email: "viewer"}
