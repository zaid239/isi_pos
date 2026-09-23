"""add_is_stock_tracked_to_items

Revision ID: b2f8a1c9e3d4
Revises: 016195699ff9
Create Date: 2026-09-21 21:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2f8a1c9e3d4'
down_revision = '016195699ff9'
branch_labels = None
depends_on = None


def upgrade():
    # Add is_stock_tracked column with server_default='1' (NUMBER(1) in Oracle)
    op.add_column(
        'items',
        sa.Column('is_stock_tracked', sa.SmallInteger(), server_default='1', nullable=False)
    )
    op.create_check_constraint(
        'ck_items_is_stock_tracked',
        'items',
        'is_stock_tracked IN (0, 1)'
    )


def downgrade():
    op.drop_constraint('ck_items_is_stock_tracked', 'items', type_='check')
    op.drop_column('items', 'is_stock_tracked')
