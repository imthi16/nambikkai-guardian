"""The workspace role matrix.

One place defines what each role may do; routes and services ask questions
about it instead of comparing roles ad hoc. `VIEWER` is the read-only
reviewer role: it can see the workspace and query evidence but changes
nothing. Member management is deliberately asymmetric: admins run the
day-to-day roster but cannot touch or mint privileged roles.
"""

import enum

from app.db.models.enums import MembershipRole


class WorkspaceAction(enum.Enum):
    """A capability a workspace member may hold."""

    VIEW = "view"
    QUERY = "query"
    UPLOAD_DOCUMENTS = "upload_documents"
    MANAGE_MEMBERS = "manage_members"


_ROLE_ACTIONS: dict[MembershipRole, frozenset[WorkspaceAction]] = {
    MembershipRole.OWNER: frozenset(WorkspaceAction),
    MembershipRole.ADMIN: frozenset(WorkspaceAction),
    MembershipRole.MEMBER: frozenset(
        {WorkspaceAction.VIEW, WorkspaceAction.QUERY, WorkspaceAction.UPLOAD_DOCUMENTS}
    ),
    MembershipRole.VIEWER: frozenset({WorkspaceAction.VIEW, WorkspaceAction.QUERY}),
}

# Roles an actor may grant, change, or remove. Only owners handle
# privileged roles, so an admin can never lock owners out or escalate.
_MANAGEABLE_ROLES: dict[MembershipRole, frozenset[MembershipRole]] = {
    MembershipRole.OWNER: frozenset(MembershipRole),
    MembershipRole.ADMIN: frozenset({MembershipRole.MEMBER, MembershipRole.VIEWER}),
    MembershipRole.MEMBER: frozenset(),
    MembershipRole.VIEWER: frozenset(),
}


def allows(role: MembershipRole, action: WorkspaceAction) -> bool:
    """Whether a role holds a capability."""
    return action in _ROLE_ACTIONS[role]


def can_manage_role(actor: MembershipRole, target: MembershipRole) -> bool:
    """Whether an actor may grant `target` or manage a member holding it."""
    return target in _MANAGEABLE_ROLES[actor]
