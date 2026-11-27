"""Widen personal loan status column.

Revision ID: e4c9a7d21f60
Revises: 7b6e2d9a4c31
"""
from alembic import op
import sqlalchemy as sa


revision = "e4c9a7d21f60"
down_revision = "7b6e2d9a4c31"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "personal_loans",
        "status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade():
    op.alter_column(
        "personal_loans",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
