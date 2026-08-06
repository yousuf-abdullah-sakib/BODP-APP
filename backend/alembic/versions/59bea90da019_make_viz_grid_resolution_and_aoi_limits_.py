"""make viz grid resolution and aoi limits nullable

Revision ID: 59bea90da019
Revises: dac3920b66e4
Create Date: 2026-08-06 14:10:29.273281

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import geoalchemy2


revision: str = '59bea90da019'
down_revision: Union[str, None] = 'dac3920b66e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Only the nullability change is intentional here — grid resolution
    # and AOI area join the date-range columns' "None = unlimited"
    # convention, letting the admin fully disable any of the 6 compute
    # limits independently, not just raise/lower them. server_default is
    # left untouched (autogenerate's proposal to drop it on the unrelated
    # viz_export_*_enabled columns, and to strip it here too, is a
    # Python-default-vs-DB-server_default drift Alembic detects but this
    # migration isn't the place to address — intentionally omitted).
    op.alter_column('site_settings', 'viz_max_grid_resolution', existing_type=sa.INTEGER(), nullable=True)
    op.alter_column('site_settings', 'viz_max_aoi_km2', existing_type=sa.DOUBLE_PRECISION(precision=53), nullable=True)


def downgrade() -> None:
    op.alter_column('site_settings', 'viz_max_aoi_km2', existing_type=sa.DOUBLE_PRECISION(precision=53), nullable=False)
    op.alter_column('site_settings', 'viz_max_grid_resolution', existing_type=sa.INTEGER(), nullable=False)
