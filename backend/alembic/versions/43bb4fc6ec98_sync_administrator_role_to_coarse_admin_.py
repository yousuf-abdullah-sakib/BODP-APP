"""sync administrator role to coarse admin access

Revision ID: 43bb4fc6ec98
Revises: e59e946c0c71
Create Date: 2026-08-10 16:40:01.256790

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = '43bb4fc6ec98'
down_revision: Union[str, None] = 'e59e946c0c71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen-in-time table reflections — never import live app.models here, see
# every prior data migration in this project for why.
_USERS_TABLE = sa.table(
    "users",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("role", sa.String),
)
_ROLES_TABLE = sa.table(
    "roles",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("name", sa.String),
)
_USER_ROLES_TABLE = sa.table(
    "user_roles",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("user_id", UUID(as_uuid=True)),
    sa.column("role_id", UUID(as_uuid=True)),
)

_ADMINISTRATOR_ROLE_NAME = "Administrator"


def upgrade() -> None:
    # Repairs data created before app/services/admin_users_service.py's
    # assign_role/unassign_role and admin_team_service.py's invite_admin
    # started keeping the coarse users.role column and fine-grained
    # "Administrator" Role membership in sync with each other. Until this
    # fix, the two were fully independent: a user could hold role='admin'
    # with zero fine-grained permissions (every admin invited via "Admin
    # Management"), or hold the "Administrator" Role's permissions with
    # role still 'user' (anyone assigned it via "Roles & Permissions"),
    # either of which left require_permission()'s combined check failing
    # for nearly the entire admin panel. This migration one-time-repairs
    # every account already in that inconsistent state; the service-layer
    # fix keeps it from recurring going forward.
    bind = op.get_bind()

    admin_role_id = bind.execute(
        sa.select(_ROLES_TABLE.c.id).where(_ROLES_TABLE.c.name == _ADMINISTRATOR_ROLE_NAME)
    ).scalar_one_or_none()
    if admin_role_id is None:
        # Administrator role doesn't exist yet on this database (shouldn't
        # happen post-6220672f5bff, but a migration must never assume
        # another migration's data survived untouched) — nothing to sync.
        return

    # Direction 1: role='admin' but no Administrator Role membership yet
    # (every pre-fix "Invite Admin" account, and any manually-elevated
    # account like an operator flipping the role column by hand) — attach
    # the Role so fine-grained-permission-gated endpoints stop rejecting
    # them.
    admins_without_role = bind.execute(
        sa.select(_USERS_TABLE.c.id)
        .select_from(
            _USERS_TABLE.outerjoin(
                _USER_ROLES_TABLE,
                sa.and_(
                    _USER_ROLES_TABLE.c.user_id == _USERS_TABLE.c.id,
                    _USER_ROLES_TABLE.c.role_id == admin_role_id,
                ),
            )
        )
        .where(_USERS_TABLE.c.role == "admin", _USER_ROLES_TABLE.c.id.is_(None))
    ).scalars().all()

    for user_id in admins_without_role:
        bind.execute(
            _USER_ROLES_TABLE.insert().values(
                id=sa.text("gen_random_uuid()"), user_id=user_id, role_id=admin_role_id
            )
        )

    # Direction 2: holds the Administrator Role but role is still 'user'
    # (anyone assigned "Administrator" via Roles & Permissions before the
    # sync fix existed) — promote the coarse column so require_permission's
    # first check (role == 'admin') stops rejecting them outright.
    bind.execute(
        _USERS_TABLE.update()
        .where(
            _USERS_TABLE.c.id.in_(
                sa.select(_USER_ROLES_TABLE.c.user_id).where(
                    _USER_ROLES_TABLE.c.role_id == admin_role_id
                )
            ),
            _USERS_TABLE.c.role != "admin",
        )
        .values(role="admin")
    )


def downgrade() -> None:
    # Deliberately a no-op: this migration only repairs pre-existing
    # inconsistent rows into a consistent state (role='admin' AND
    # Administrator Role attached together). There's no prior state to
    # restore — reversing it would mean re-introducing the exact
    # inconsistency this migration exists to fix, which downgrade() should
    # not do.
    pass
