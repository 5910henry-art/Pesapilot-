"""add customer-safe application decision message

Revision ID: c81d6f2a9b40
Revises: ebd56bbde521
"""

from alembic import op
import sqlalchemy as sa


revision = "c81d6f2a9b40"
down_revision = "ebd56bbde521"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "platform_loan_applications",
        sa.Column("customer_decision_message", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column(
        "platform_loan_applications",
        "customer_decision_message",
    )
