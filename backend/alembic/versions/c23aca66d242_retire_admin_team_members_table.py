"""retire admin team members table

Revision ID: c23aca66d242
Revises: 1d2b8a17ff9c
Create Date: 2026-08-11 10:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'c23aca66d242'
down_revision: Union[str, None] = '1d2b8a17ff9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADMIN_TEAM_TABLE = sa.table(
    "admin_team_members",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("user_id", UUID(as_uuid=True)),
    sa.column("role_label", sa.String),
)
_ROLES_TABLE = sa.table("roles", sa.column("id", UUID(as_uuid=True)), sa.column("name", sa.String))
_USER_ROLES_TABLE = sa.table(
    "user_roles",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("user_id", UUID(as_uuid=True)),
    sa.column("role_id", UUID(as_uuid=True)),
)
_USERS_TABLE = sa.table("users", sa.column("id", UUID(as_uuid=True)), sa.column("role", sa.String))

# AdminTeamMember.role_label was always free text with no FK to `roles` —
# admin_team_service.py's Edit UI offered exactly these 4 strings, so any
# row whose label happens to match one of the now-real Role names
# represents genuine admin intent worth preserving as a real assignment.
_KNOWN_ADMIN_ROLE_NAMES = {"Administrator", "Data Manager", "Reviewer", "Content Editor"}


def upgrade() -> None:
    # This migration exists to retire AdminTeamMember (see MASTER_PLAN /
    # session notes: Admin Management and Roles & Permissions were two
    # disconnected systems — a bespoke roster table with a free-text
    # role_label, versus the real Role/UserRole RBAC tables). Before
    # dropping the roster table, reconstruct real Role assignments for any
    # row that still points at a real User, so nobody silently loses the
    # admin-panel access implied by their AdminTeamMember row.
    bind = op.get_bind()

    role_ids_by_name = dict(
        bind.execute(sa.select(_ROLES_TABLE.c.name, _ROLES_TABLE.c.id)).all()
    )

    rows = bind.execute(
        sa.select(_ADMIN_TEAM_TABLE.c.user_id, _ADMIN_TEAM_TABLE.c.role_label).where(
            _ADMIN_TEAM_TABLE.c.user_id.is_not(None)
        )
    ).all()

    for user_id, role_label in rows:
        # Prefer the role_label's matching real Role; fall back to
        # Administrator for any row whose label doesn't match one of the
        # 4 known names (e.g. blank, or a value entered before this UI
        # existed) — these users are already coarse role='admin' today,
        # so leaving them with zero fine-grained permissions would strand
        # them with less access than they effectively have right now.
        target_role_name = role_label if role_label in _KNOWN_ADMIN_ROLE_NAMES else "Administrator"
        role_id = role_ids_by_name.get(target_role_name)
        if role_id is None:
            continue

        already_assigned = bind.execute(
            sa.select(_USER_ROLES_TABLE.c.id).where(
                _USER_ROLES_TABLE.c.user_id == user_id, _USER_ROLES_TABLE.c.role_id == role_id
            )
        ).first()
        if already_assigned is None:
            bind.execute(
                _USER_ROLES_TABLE.insert().values(
                    id=sa.text("gen_random_uuid()"), user_id=user_id, role_id=role_id
                )
            )

        # Every reconstructed assignment is a non-"User" role, so mirror
        # admin_users_service.assign_role's coarse-role sync here too.
        bind.execute(
            _USERS_TABLE.update()
            .where(_USERS_TABLE.c.id == user_id, _USERS_TABLE.c.role != "admin")
            .values(role="admin")
        )

    op.drop_table("admin_team_members")


def downgrade() -> None:
    op.create_table(
        "admin_team_members",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role_label", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Deliberately does not attempt to reconstruct rows — the upgrade's
    # Role-assignment reconstruction has no clean inverse (multiple
    # AdminTeamMember rows could have mapped to the same UserRole, and
    # this table's whole purpose was superseded by real Role data).
