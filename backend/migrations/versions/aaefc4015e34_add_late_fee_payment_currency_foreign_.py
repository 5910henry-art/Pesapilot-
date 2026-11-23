"""add late fee payment currency foreign key

Revision ID: aaefc4015e34
Revises: e48a25f491cc
Create Date: 2026-11-23 13:31:17.857398

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'aaefc4015e34'
down_revision = 'e48a25f491cc'
branch_labels = None
depends_on = None


def upgrade():
    op.create_foreign_key(
        "fk_platform_late_fee_payment_currency",
        "platform_loan_late_fee_payments",
        "currencies",
        ["currency_code"],
        ["code"],
    )


def downgrade():
    op.drop_constraint(
        "fk_platform_late_fee_payment_currency",
        "platform_loan_late_fee_payments",
        type_="foreignkey",
    )
