"""add support ticket reply columns and contact submissions

Revision ID: db95d2a1579d
Revises: 29bbbb8a3f6b
Create Date: 2026-08-10 17:36:08.837476

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'db95d2a1579d'
down_revision: Union[str, None] = '29bbbb8a3f6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Autogenerate also proposed stripping server_default from several
# site_settings.viz_* columns — the same pre-existing model/DB
# server_default drift noise seen in every prior migration in this
# project (SQLAlchemy-side Python defaults vs. DB-side server defaults
# that were never actually declared as server_default= in the model).
# Not this migration's concern; omitted here as in every prior one.


def upgrade() -> None:
    op.create_table('contact_submissions',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('organization', sa.String(length=200), nullable=True),
    sa.Column('subject', sa.String(length=100), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('reply_message', sa.Text(), nullable=True),
    sa.Column('replied_by_id', sa.UUID(), nullable=True),
    sa.Column('replied_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['replied_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_contact_submissions_email'), 'contact_submissions', ['email'], unique=False)
    op.add_column('support_tickets', sa.Column('reply_message', sa.Text(), nullable=True))
    op.add_column('support_tickets', sa.Column('replied_by_id', sa.UUID(), nullable=True))
    op.add_column('support_tickets', sa.Column('replied_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        'support_tickets_replied_by_id_fkey', 'support_tickets', 'users',
        ['replied_by_id'], ['id'], ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint('support_tickets_replied_by_id_fkey', 'support_tickets', type_='foreignkey')
    op.drop_column('support_tickets', 'replied_at')
    op.drop_column('support_tickets', 'replied_by_id')
    op.drop_column('support_tickets', 'reply_message')
    op.drop_index(op.f('ix_contact_submissions_email'), table_name='contact_submissions')
    op.drop_table('contact_submissions')
