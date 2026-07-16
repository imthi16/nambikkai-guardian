"""The role matrix is the single authorization contract; pin it exactly."""

import pytest
from app.auth.permissions import WorkspaceAction, allows, can_manage_role
from app.db.models.enums import MembershipRole

FULL = {
    WorkspaceAction.VIEW,
    WorkspaceAction.QUERY,
    WorkspaceAction.UPLOAD_DOCUMENTS,
    WorkspaceAction.MANAGE_MEMBERS,
}

EXPECTED_MATRIX = {
    MembershipRole.OWNER: FULL,
    MembershipRole.ADMIN: FULL,
    MembershipRole.MEMBER: {
        WorkspaceAction.VIEW,
        WorkspaceAction.QUERY,
        WorkspaceAction.UPLOAD_DOCUMENTS,
    },
    MembershipRole.VIEWER: {WorkspaceAction.VIEW, WorkspaceAction.QUERY},
}


@pytest.mark.parametrize("role", list(MembershipRole))
@pytest.mark.parametrize("action", list(WorkspaceAction))
def test_role_matrix_is_exactly_as_documented(
    role: MembershipRole,
    action: WorkspaceAction,
) -> None:
    assert allows(role, action) is (action in EXPECTED_MATRIX[role])


EXPECTED_MANAGEABLE = {
    MembershipRole.OWNER: set(MembershipRole),
    MembershipRole.ADMIN: {MembershipRole.MEMBER, MembershipRole.VIEWER},
    MembershipRole.MEMBER: set(),
    MembershipRole.VIEWER: set(),
}


@pytest.mark.parametrize("actor", list(MembershipRole))
@pytest.mark.parametrize("target", list(MembershipRole))
def test_role_management_rules(actor: MembershipRole, target: MembershipRole) -> None:
    assert can_manage_role(actor, target) is (target in EXPECTED_MANAGEABLE[actor])
